"""Brain-owned, retry-safe body staging handles."""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from _common import resolve_body_file, safe_write


STAGING_DIR = os.path.join(".brain", "local", "staging")
_HANDLE_RE = re.compile(r"^body:([a-f0-9]{32})$")
MAX_STAGED_BODY_BYTES = 16 * 1024 * 1024
MAX_STAGING_BYTES = 64 * 1024 * 1024
MAX_STAGING_FILES = 100
STAGING_TTL_SECONDS = 24 * 60 * 60


def _handle_path(vault_root, handle):
    match = _HANDLE_RE.fullmatch(validate_staged_body_handle(handle))
    return os.path.join(vault_root, STAGING_DIR, f"{match.group(1)}.body")


def validate_staged_body_handle(handle):
    """Return a syntactically valid opaque staged-body handle."""
    match = _HANDLE_RE.fullmatch(handle or "")
    if not match:
        raise ValueError(
            "Invalid stage handle. Create one with stage.create and pass the returned handle."
        )
    return handle


def _iter_staged_bodies(directory):
    """Yield only files that belong to the staged-body handle namespace."""
    for item in directory.iterdir():
        if item.is_file() and item.suffix == ".body":
            yield item


def stage_body(vault_root, content):
    encoded_size = len(content.encode("utf-8"))
    if encoded_size > MAX_STAGED_BODY_BYTES:
        raise ValueError(
            f"Staged body is {encoded_size} bytes; maximum is {MAX_STAGED_BODY_BYTES}"
        )
    staging_dir = Path(vault_root) / STAGING_DIR
    staging_dir.mkdir(parents=True, exist_ok=True)
    sweep_staged_bodies(vault_root)
    existing = list(_iter_staged_bodies(staging_dir))
    total_bytes = sum(item.stat().st_size for item in existing)
    if len(existing) >= MAX_STAGING_FILES or total_bytes + encoded_size > MAX_STAGING_BYTES:
        raise ValueError(
            "Brain staging limit reached; consume or discard existing body handles"
        )
    token = secrets.token_hex(16)
    handle = f"body:{token}"
    path = _handle_path(vault_root, handle)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    safe_write(path, content, bounds=vault_root)
    return {"handle": handle, "bytes": encoded_size, "expires_in_seconds": STAGING_TTL_SECONDS}


def read_staged_body(vault_root, handle):
    path = _handle_path(vault_root, handle)
    try:
        staged_path = Path(path)
        if staged_path.stat().st_mtime < time.time() - STAGING_TTL_SECONDS:
            staged_path.unlink(missing_ok=True)
            raise ValueError(
                f"Expired body_handle '{handle}'. Stage the content again."
            )
        return staged_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(
            f"Unknown or already-consumed body_handle '{handle}'. Stage the content again."
        ) from None


def resolve_mutation_body(vault_root, *, body="", body_file="", body_handle=""):
    """Resolve the mutually exclusive public mutation body sources."""
    supplied = sum(bool(value) for value in (body, body_file, body_handle))
    if supplied > 1:
        raise ValueError(
            "body, body_file, and body_handle are mutually exclusive; pass exactly one"
        )
    if body_handle:
        return read_staged_body(vault_root, body_handle), body_handle
    resolved, _cleanup = resolve_body_file(body, body_file, vault_root=vault_root)
    return resolved, None


def consume_staged_body(vault_root, handle):
    path = _handle_path(vault_root, handle)
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    return True


def discard_staged_body(vault_root, handle):
    """Explicitly discard an unused staged body handle."""
    return consume_staged_body(vault_root, handle)


def sweep_staged_bodies(vault_root, *, now=None):
    """Remove expired staging files and return the number discarded."""
    directory = Path(vault_root) / STAGING_DIR
    if not directory.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - STAGING_TTL_SECONDS
    removed = 0
    for item in _iter_staged_bodies(directory):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def finalise_staged_body(vault_root, handle):
    """Best-effort post-commit cleanup; never turn a successful mutation into failure."""
    if not handle:
        return None
    try:
        consumed = consume_staged_body(vault_root, handle)
    except OSError as exc:
        return f"Mutation succeeded, but staged body cleanup failed: {exc}"
    if not consumed:
        return "Mutation succeeded; the staged body handle was already absent."
    return None
