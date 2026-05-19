"""Dependency-free authentication helpers."""

from __future__ import annotations

import hashlib


def hash_key(key: str) -> str:
    """SHA-256 hash of an operator key, formatted as ``sha256:<hexdigest>``."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
