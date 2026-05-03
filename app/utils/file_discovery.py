"""File discovery helpers for local financial directories."""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".csv", ".json", ".xlsx"}


def discover_financial_files(base_dir: str) -> list[Path]:
    """Recursively discover supported financial source files."""

    root = Path(base_dir)
    if not root.exists() or not root.is_dir():
        return []
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        results.append(path)
    return sorted(results)

