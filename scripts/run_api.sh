#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8005}"
RELOAD="${RELOAD:-1}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Project virtualenv not found: ${PYTHON_BIN}" >&2
  echo "Create it first, for example: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

cd "${ROOT_DIR}"

if ! "${PYTHON_BIN}" scripts/check_runtime.py; then
  echo "Runtime check failed. Install dependencies into .venv with: .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ "${RELOAD}" == "1" ]]; then
  exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" --reload
fi

exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
