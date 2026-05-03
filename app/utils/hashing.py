"""Hashing helpers used by filing storage and deterministic embeddings."""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return SHA256 for raw bytes."""

    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    """Return SHA256 for UTF-8 text."""

    return sha256_bytes(value.encode("utf-8"))
