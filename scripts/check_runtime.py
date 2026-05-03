"""Check whether the active Python runtime can run the filing RAG stack."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def main() -> int:
    """Print a compact runtime diagnostic and return non-zero on blockers."""

    project_root = Path(__file__).resolve().parents[1]
    checks = {
        "python_executable": sys.executable,
        "project_root": str(project_root),
        "chromadb_available": importlib.util.find_spec("chromadb") is not None,
        "uvicorn_available": importlib.util.find_spec("uvicorn") is not None,
        "app_available": importlib.util.find_spec("app.main") is not None,
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if all(value for key, value in checks.items() if key.endswith("_available")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
