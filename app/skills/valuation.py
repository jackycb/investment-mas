"""valuation_skill — wraps the existing Python valuation program, unchanged.

Optimization task §4.3. Hard constraints honored here:

  * **No formula is re-implemented.** Every number comes from
    :meth:`ValuationService.run`, which itself calls the bundled legacy program
    ``scripts/run_value_analysis_by_code.py`` +
    ``scripts/value_investment_market_value.py`` through
    :class:`SupplementalValuationService`.
  * **No new valuation model** (no DCF, Owner Earnings, EV/EBITDA) and no new
    method-selection logic.
  * **No LLM involvement** in any numeric step.
  * The buy/sell price *meanings* are preserved: this module only exposes
    read-only views onto values the existing program already produced.

Buy/sell mapping (documented, not invented)
-------------------------------------------
``ValuationService._price_signal`` already defines the semantics: a price at or
below ``blended.bear`` yields ``strong_buy``, and a price at or above
``blended.bull`` yields ``avoid``. Therefore:

    buy_price  -> valuation_result["blended"]["bear"]   (lower bound / entry)
    base_price -> valuation_result["blended"]["base"]
    sell_price -> valuation_result["blended"]["bull"]   (upper bound / exit)

When the legacy EPV program ran successfully its raw outputs are also surfaced
verbatim as ``epv_lower_price_limit`` / ``epv_upper_price_limit`` /
``epv_midpoint_value``.

Caching (§6.3)
--------------
The cache key covers the valuation inputs, the effective assumptions, the
valuation date (the result embeds ``current_price`` and ``price_vs_valuation``,
so it is price-sensitive) and a fingerprint of the legacy program source. A
stale same-company result is therefore never reused unconditionally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.compute_cache_service import CACHE_VALUATION, cache_key, cached_compute
from app.services.valuation_service import ValuationService
from app.skills.base import PermanentSkillError, skill_span
from app.utils.hashing import sha256_bytes

logger = get_logger(__name__)

# Key under which the reusable payload is stored inside the existing
# ``valuation_results.assumptions`` JSONB column. No schema change required.
CACHE_ASSUMPTION_KEY = "_cache"


def _fingerprint_file(path: str | Path | None) -> str:
    """Hash a source file so a change to the valuation program busts the cache."""

    if not path:
        return "absent"
    file_path = Path(path)
    if not file_path.exists():
        return "missing"
    try:
        return sha256_bytes(file_path.read_bytes())[:16]
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("valuation_program_fingerprint_failed", path=str(file_path), error=str(exc))
        return "unreadable"


def valuation_program_fingerprint() -> str:
    """Version stamp of the legacy buy/sell price program (§6.3 requirement)."""

    settings = get_settings()
    script = _fingerprint_file(settings.supplemental_valuation_script_path)
    helper = _fingerprint_file(settings.supplemental_valuation_helper_path)
    return f"{script}:{helper}"


@dataclass
class ValuationSkillResult:
    """Valuation output plus provenance. Numeric fields are pass-through."""

    ticker: str
    company_name: str
    #: Verbatim first element of ``ValuationService.run``.
    valuation_result: dict = field(default_factory=dict)
    #: Verbatim second element of ``ValuationService.run`` (plus ``_cache``).
    assumptions: dict = field(default_factory=dict)
    status: str = "ok"  # ok | epv_unavailable | skipped | failed
    epv_available: bool = False
    cache_hit: bool = False
    cache_key: str | None = None
    program_fingerprint: str | None = None
    valuation_date: str = field(default_factory=lambda: date.today().isoformat())
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ---------------------------------------------------- read-only price views

    @property
    def blended(self) -> dict:
        return dict(self.valuation_result.get("blended") or {})

    @property
    def buy_price(self) -> float | None:
        """Existing lower bound (``blended.bear``) — not recomputed here."""

        return self.blended.get("bear")

    @property
    def base_price(self) -> float | None:
        return self.blended.get("base")

    @property
    def sell_price(self) -> float | None:
        """Existing upper bound (``blended.bull``) — not recomputed here."""

        return self.blended.get("bull")

    @property
    def epv_lower_price_limit(self) -> float | None:
        legacy = self.valuation_result.get("legacy_value_analysis") or {}
        return legacy.get("lower_price_limit")

    @property
    def epv_upper_price_limit(self) -> float | None:
        legacy = self.valuation_result.get("legacy_value_analysis") or {}
        return legacy.get("upper_price_limit")

    @property
    def epv_midpoint_value(self) -> float | None:
        legacy = self.valuation_result.get("legacy_value_analysis") or {}
        return legacy.get("midpoint_value")

    @property
    def price_signal(self) -> dict:
        return dict(self.valuation_result.get("price_vs_valuation") or {})

    @property
    def current_price(self) -> float | None:
        return self.valuation_result.get("current_price")

    def as_state_patch(self) -> dict[str, Any]:
        """Payload shaped exactly like ``ValuationAgentNode``'s return value."""

        return {
            "valuation_result": self.valuation_result,
            "valuation_assumptions": self.assumptions,
        }

    def numeric_snapshot(self) -> dict[str, Any]:
        """The numbers a regression test must compare, tolerance-aware."""

        return {
            "buy_price": self.buy_price,
            "base_price": self.base_price,
            "sell_price": self.sell_price,
            "epv_lower_price_limit": self.epv_lower_price_limit,
            "epv_upper_price_limit": self.epv_upper_price_limit,
            "epv_midpoint_value": self.epv_midpoint_value,
            "primary_value": dict(self.valuation_result.get("primary_value") or {}),
            "growth": dict(self.valuation_result.get("growth") or {}),
            "blended_total_value": dict(self.valuation_result.get("blended_total_value") or {}),
            "valuation_method": self.valuation_result.get("valuation_method"),
            "is_per_share": self.valuation_result.get("is_per_share"),
            "shares_outstanding": self.valuation_result.get("shares_outstanding"),
            "fx_rate": self.valuation_result.get("fx_rate"),
            "per_share_currency": self.valuation_result.get("per_share_currency"),
        }



class ValuationSkill:
    """Reusable entry point around the existing valuation program.

    The skill validates inputs, computes the cache key, delegates every numeric
    step to :class:`ValuationService`, and records the run so identical inputs
    can be reused. It never touches a formula.
    """

    def __init__(self, service: ValuationService | None = None, session: Session | None = None) -> None:
        self.service = service or ValuationService()
        self.session = session

    def cache_key_for(
        self,
        ticker: str,
        financial_summary: dict,
        peer_comparison: dict,
        company_profile: dict | None,
        *,
        valuation_date: str | None = None,
    ) -> str:
        """Cache key over inputs + assumptions + date + program version (§6.3)."""

        return cache_key(
            "valuation",
            ticker,
            valuation_date or date.today().isoformat(),
            valuation_program_fingerprint(),
            bool(get_settings().enable_supplemental_valuation),
            financial_summary.get("years"),
            financial_summary.get("revenue_cagr"),
            financial_summary.get("latest_metrics"),
            (peer_comparison or {}).get("metrics"),
            # Only the profile fields the existing growth model actually reads.
            {k: (company_profile or {}).get(k) for k in ("business_model", "market_space", "competition_landscape")},
        )

    def run(
        self,
        financial_summary: dict,
        peer_comparison: dict,
        company_profile: dict | None = None,
        *,
        ticker: str | None = None,
        company_name: str | None = None,
        force_refresh: bool = False,
        allow_db_reuse: bool = True,
    ) -> ValuationSkillResult:
        """Run (or reuse) the valuation and return pass-through results."""

        if not ticker:
            raise PermanentSkillError("valuation_skill requires a ticker")

        financial_summary = financial_summary or {}
        peer_comparison = peer_comparison or {}
        key = self.cache_key_for(ticker, financial_summary, peer_comparison, company_profile)
        fingerprint = valuation_program_fingerprint()

        with skill_span("valuation") as record:
            payload, hit = cached_compute(
                CACHE_VALUATION,
                key,
                lambda: self._invoke(
                    financial_summary,
                    peer_comparison,
                    company_profile,
                    ticker=ticker,
                    company_name=company_name or ticker,
                    key=key,
                    fingerprint=fingerprint,
                ),
                force_refresh=force_refresh,
                # A failed or empty valuation must never be cached as reusable.
                cache_ok=lambda value: bool(value.get("valuation_result")),
            )
            if not hit and allow_db_reuse:
                restored = self._load_db_cached(ticker, key)
                if restored is not None:
                    payload, hit = restored, True
                    record.cache_hit = True
                    record.detail = "reused_from_valuation_results_row"
                    logger.info("valuation_db_cache_hit", ticker=ticker, cache_key=key)

        epv_available = bool((payload.get("valuation_result") or {}).get("epv_available"))
        result = ValuationSkillResult(
            ticker=ticker,
            company_name=company_name or ticker,
            valuation_result=payload.get("valuation_result") or {},
            assumptions=payload.get("assumptions") or {},
            status=payload.get("status", "ok"),
            epv_available=epv_available,
            cache_hit=hit,
            cache_key=key,
            program_fingerprint=fingerprint,
            warnings=list(payload.get("warnings") or []),
            errors=list(payload.get("errors") or []),
        )
        logger.info(
            "valuation_skill_done",
            ticker=ticker,
            status=result.status,
            epv_available=result.epv_available,
            buy_price=result.buy_price,
            base_price=result.base_price,
            sell_price=result.sell_price,
            cache_hit=hit,
        )
        return result


    def _invoke(
        self,
        financial_summary: dict,
        peer_comparison: dict,
        company_profile: dict | None,
        *,
        ticker: str,
        company_name: str,
        key: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        """Call the existing service. The only mutation is additive metadata."""

        try:
            valuation_result, assumptions = self.service.run(
                financial_summary,
                peer_comparison,
                company_profile,
                ticker=ticker,
                company_name=company_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("valuation_skill_failed", ticker=ticker, error=str(exc))
            return {
                "valuation_result": {},
                "assumptions": {},
                "status": "failed",
                "warnings": [],
                "errors": [f"valuation_skill failed: {exc}"],
            }

        assumptions = dict(assumptions or {})
        epv_available = bool(valuation_result.get("epv_available"))
        inner = {k: v for k, v in assumptions.items() if k != CACHE_ASSUMPTION_KEY}
        # Additive only: stored in the existing JSONB assumptions column so a
        # later run can verify a persisted result still matches current inputs.
        assumptions[CACHE_ASSUMPTION_KEY] = {
            "key": key,
            "program_fingerprint": fingerprint,
            "valuation_date": date.today().isoformat(),
            "payload": {"valuation_result": valuation_result, "assumptions": inner},
        }
        return {
            "valuation_result": valuation_result,
            "assumptions": assumptions,
            "status": "ok" if epv_available else "epv_unavailable",
            "warnings": [] if epv_available else ["EPV legacy valuation unavailable; multiples blend used."],
            "errors": [],
        }

    def _load_db_cached(self, ticker: str, key: str) -> dict[str, Any] | None:
        """Reuse a persisted valuation only when its recorded key matches exactly.

        Reuses the existing ``valuation_results`` table — no new table, no new
        column. Returns ``None`` when nothing safely reusable exists so the
        caller recomputes rather than serving a partial result.
        """

        if self.session is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models import ValuationResult
            from app.db.repositories.company_repository import get_company_by_ticker

            company = get_company_by_ticker(self.session, ticker)
            if company is None:
                return None
            row = self.session.scalar(
                select(ValuationResult)
                .where(ValuationResult.company_id == company.id)
                .order_by(ValuationResult.created_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            cached = (row.assumptions or {}).get(CACHE_ASSUMPTION_KEY) or {}
            if cached.get("key") != key:
                return None
            payload = cached.get("payload") or {}
            if not payload.get("valuation_result"):
                return None
            assumptions = dict(payload.get("assumptions") or {})
            assumptions[CACHE_ASSUMPTION_KEY] = cached
            epv_available = bool(payload["valuation_result"].get("epv_available"))
            return {
                "valuation_result": payload["valuation_result"],
                "assumptions": assumptions,
                "status": "ok" if epv_available else "epv_unavailable",
                "warnings": [],
                "errors": [],
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("valuation_db_cache_lookup_failed", ticker=ticker, error=str(exc)[:160])
            return None


_default_valuation_skill: ValuationSkill | None = None


def get_valuation_skill(session: Session | None = None) -> ValuationSkill:
    """Return a skill bound to *session*, or a process-wide session-less default."""

    global _default_valuation_skill
    if session is not None:
        return ValuationSkill(session=session)
    if _default_valuation_skill is None:
        _default_valuation_skill = ValuationSkill()
    return _default_valuation_skill


def run_valuation(
    financial_summary: dict,
    peer_comparison: dict,
    company_profile: dict | None = None,
    *,
    ticker: str | None = None,
    company_name: str | None = None,
    session: Session | None = None,
    force_refresh: bool = False,
) -> ValuationSkillResult:
    """Functional entry point for the valuation skill."""

    return get_valuation_skill(session).run(
        financial_summary,
        peer_comparison,
        company_profile,
        ticker=ticker,
        company_name=company_name,
        force_refresh=force_refresh,
    )

