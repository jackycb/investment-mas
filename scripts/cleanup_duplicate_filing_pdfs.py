"""Find and optionally remove duplicate `.raw.pdf` filing files.

The current storage format keeps exactly one PDF original plus one derived TXT
file. Older runs wrote both `*.raw.pdf` and `*_<checksum>.pdf` for the same
download. This script rewrites metadata to the canonical PDF and removes the
duplicate raw copy only when byte hashes match.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.db.models import Filing
from app.utils.hashing import sha256_bytes
from app.utils.metadata_utils import json_safe


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean duplicate raw PDF filing files.")
    parser.add_argument("--apply", action="store_true", help="Actually update metadata and delete duplicate raw PDFs.")
    return parser.parse_args()


def _hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _candidate_canonical(raw_pdf: Path) -> Path | None:
    stem = raw_pdf.name.removesuffix(".raw.pdf")
    for candidate in raw_pdf.parent.glob(f"{stem}_*.pdf"):
        if candidate.name.endswith(".raw.pdf"):
            continue
        if candidate.exists() and _hash(candidate) == _hash(raw_pdf):
            return candidate
    plain = raw_pdf.parent / f"{stem}.pdf"
    if plain.exists() and _hash(plain) == _hash(raw_pdf):
        return plain
    return None


def _rewrite_sidecars(root: Path, raw_pdf: Path, canonical_pdf: Path, apply: bool) -> int:
    count = 0
    for sidecar in root.rglob("*.meta.json"):
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        changed = False
        for key in ("raw_local_path", "local_path"):
            if payload.get(key) == str(raw_pdf):
                payload[key] = str(canonical_pdf)
                changed = True
        if changed:
            count += 1
            if apply:
                sidecar.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return count


def _rewrite_db(raw_pdf: Path, canonical_pdf: Path, apply: bool) -> int:
    session = SessionLocal()
    count = 0
    try:
        rows = list(session.scalars(select(Filing)).all())
        for filing in rows:
            metadata = dict(filing.metadata_json or {})
            changed = False
            if filing.local_path == str(raw_pdf):
                filing.local_path = str(canonical_pdf)
                changed = True
            for key in ("raw_local_path", "local_path"):
                if metadata.get(key) == str(raw_pdf):
                    metadata[key] = str(canonical_pdf)
                    changed = True
            if changed:
                filing.metadata_json = json_safe(metadata)
                count += 1
        if apply:
            session.commit()
        else:
            session.rollback()
        return count
    finally:
        session.close()


def main() -> None:
    args = _parse_args()
    root = Path(get_settings().document_storage_dir)
    results: list[dict[str, Any]] = []
    for raw_pdf in root.rglob("*.raw.pdf"):
        canonical = _candidate_canonical(raw_pdf)
        if canonical is None:
            continue
        sidecar_updates = _rewrite_sidecars(root, raw_pdf, canonical, args.apply)
        db_updates = _rewrite_db(raw_pdf, canonical, args.apply)
        if args.apply:
            raw_pdf.unlink()
        results.append(
            {
                "raw_pdf": str(raw_pdf),
                "canonical_pdf": str(canonical),
                "sidecar_updates": sidecar_updates,
                "db_updates": db_updates,
                "deleted": bool(args.apply),
            }
        )

    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "duplicate_count": len(results),
        "items": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
