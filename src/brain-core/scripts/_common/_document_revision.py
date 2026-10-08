"""Opaque revisions for optimistic concurrency on Brain documents."""

from __future__ import annotations

import hashlib
from pathlib import Path


from ._text_encoding import diagnose_text, UTF8_BOM, UTF16_BOM, UTF32_BOM, TRUNCATED_UTF8

REVISION_PREFIX = "sha256:"


class NonStandardVaultTextError(ValueError):
    """A named refusal of bytes outside the persisted document encoding contract."""

    def __init__(self, source_path, code):
        self.source_path = str(source_path) if source_path is not None else "<document>"
        self.code = code
        self.remedy = "vault.repair-text" if code in {UTF8_BOM, UTF16_BOM, UTF32_BOM, TRUNCATED_UTF8} else "vault.check"
        super().__init__(f"Cannot read '{self.source_path}': {code}; use {self.remedy}.")


class DocumentRevisionConflict(ValueError):
    """Raised before mutation when a document no longer has the expected revision."""


class PersistedDocumentContent(str):
    """Decoded document text carrying the revision of its exact persisted bytes."""

    revision: str
    source_path: str | None = None
    conversion_code: str | None = None

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


def decode_persisted_document(raw: bytes, *, source_path: str | Path | None = None,
                              convert_lossless: bool = False) -> PersistedDocumentContent:
    """Decode strict UTF-8, optionally converting lossless marked text with provenance."""
    code = None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = None
    if content is None or content.startswith("\ufeff"):
        diagnosis = diagnose_text(raw)
        if diagnosis is None:
            raise RuntimeError("non-standard document has no text diagnosis")
        if (not convert_lossless or not diagnosis.lossless
                or diagnosis.code not in {UTF8_BOM, UTF16_BOM, UTF32_BOM}):
            raise NonStandardVaultTextError(source_path, diagnosis.code)
        content = diagnosis.fixed_bytes.decode("utf-8")
        code = diagnosis.code
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    value = PersistedDocumentContent(content, document_revision(raw))
    value.source_path = str(source_path) if source_path is not None else None
    value.conversion_code = code
    return value


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
