"""Shared conventions for reusable skills.

A *skill* here is a plain Python capability module with an explicit input,
an explicit output, deterministic behaviour where possible, bounded error
handling and measurable runtime. Skills wrap existing Services — they never
re-implement formulas and never add an abstraction layer whose only purpose is
to forward a call (optimization task §11).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.services.performance_tracker import SPAN_KIND_SKILL, span


class SkillError(Exception):
    """Base class for skill failures."""


class TransientSkillError(SkillError):
    """Retryable failure: network timeout, rate limit, temporary 5xx.

    Callers may retry a bounded number of times.
    """


class PermanentSkillError(SkillError):
    """Non-recoverable failure: bad parameters, schema mismatch, missing input.

    Callers must fail fast and must not retry.
    """


class SkillUnavailableError(PermanentSkillError):
    """A required asset (script, workbook, credential) is not present."""


# Errors that indicate a transient condition and are therefore worth retrying.
TRANSIENT_ERROR_NAMES = frozenset(
    {
        "Timeout",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectError",
        "RemoteProtocolError",
        "TooManyRequests",
        "ServiceUnavailable",
        "BadGateway",
        "GatewayTimeout",
        "InternalServerError",
        "ConnectionError",
        "ConnectionResetError",
    }
)


def classify_error(exc: BaseException) -> TransientSkillError | PermanentSkillError:
    """Map an arbitrary exception onto the retryable / non-retryable split."""

    name = type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None and int(status) in {429, 500, 502, 503, 504}:
        return TransientSkillError(f"{name}: HTTP {status}")
    if status is not None and 400 <= int(status) < 500:
        return PermanentSkillError(f"{name}: HTTP {status}")
    if name in TRANSIENT_ERROR_NAMES:
        return TransientSkillError(f"{name}: {exc}")
    return PermanentSkillError(f"{name}: {exc}")


def retry_transient(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> Any:
    """Run *fn* with a bounded retry budget for transient errors only.

    Permanent errors propagate immediately (§7.2: parameter and schema errors
    must not be retried). Sleep uses a simple linear backoff to avoid adding a
    dependency.
    """

    import time

    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            classified = classify_error(exc)
            if isinstance(classified, PermanentSkillError) or attempt >= attempts:
                raise classified from exc
            last = classified
            if on_retry is not None:
                on_retry(attempt, exc)
            time.sleep(backoff_seconds * attempt)
    if last:  # pragma: no cover - defensive
        raise last
    return None


@dataclass
class SkillResult:
    """Common envelope returned by skills.

    ``data`` carries the skill-specific payload; the remaining fields are the
    uniform status surface required by optimization task §4.5.
    """

    data: Any = None
    status: str = "succeeded"  # succeeded | failed | skipped | partial
    cache_hit: bool = False
    duration_ms: float | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error_type: str | None = None
    retry_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"succeeded", "partial"}


@contextmanager
def skill_span(
    skill_name: str, *, node_name: str | None = None, span_kind: str = SPAN_KIND_SKILL
) -> Iterator[Any]:
    """Measure a skill invocation and report it under the active run.

    ``span_kind`` lets a skill separate its network/download portion
    (``SPAN_KIND_IO``) from its compute portion, which is what makes the
    "download vs parse" cost split in optimization task §10.1 measurable.
    """

    with span(node_name or skill_name, span_kind=span_kind, skill_name=skill_name) as record:
        yield record
