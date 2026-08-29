"""Opaque revisions for optimistic concurrency on Brain documents."""

from __future__ import annotations

import hashlib
from pathlib import Path


REVISION_PREFIX = "sha256:"


class DocumentRevisionConflict(ValueError):
    """Raised before mutation when a document no longer has the expected revision."""


class PersistedDocumentContent(str):
    """Decoded document text carrying the revision of its exact persisted bytes."""

    revision: str

    def __new__(cls, content: str, revision: str):
        value = super().__new__(cls, content)
        value.revision = revision
        return value


def document_revision(content: str | bytes) -> str:
    """Return a stable opaque revision for the exact persisted document content."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return f"{REVISION_PREFIX}{hashlib.sha256(raw).hexdigest()}"


def document_revision_at(path: str | Path) -> str:
    """Return the revision of the exact bytes currently persisted at *path*."""
    return document_revision(Path(path).read_bytes())


def decode_persisted_document(raw: bytes) -> PersistedDocumentContent:
    """Decode persisted UTF-8 bytes while preserving text-mode newline semantics."""
    content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return PersistedDocumentContent(content, document_revision(raw))


def validate_document_revision(value: str, *, label: str = "revision") -> None:
    """Validate the public opaque revision representation."""
    if (
        not isinstance(value, str)
        or not value.startswith(REVISION_PREFIX)
        or len(value) != len(REVISION_PREFIX) + 64
    ):
        raise ValueError(f"{label} must be a sha256 document revision")
    digest = value[len(REVISION_PREFIX):]
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} must be a sha256 document revision")


def require_document_revision(path: str | Path, expected_revision: str) -> str:
    """Require *path* to match *expected_revision* and return its current revision."""
    validate_document_revision(expected_revision, label="expected_revision")
    current_revision = document_revision_at(path)
    if current_revision != expected_revision:
        raise DocumentRevisionConflict(
            "document changed since it was read; re-read it and retry with the "
            f"current revision ({current_revision})"
        )
    return current_revision
