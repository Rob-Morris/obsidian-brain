"""Canonical retrieval filter contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SearchFilters:
    """Optional artefact filters shared across retrieval entrypoints."""

    type: str | None = None
    tag: str | None = None
    status: str | None = None

    def matches(self, doc_meta: Mapping[str, Any]) -> bool:
        """Return whether one document or result metadata dict matches."""
        if self.type and doc_meta.get("type") != self.type:
            return False
        if self.tag and self.tag not in doc_meta.get("tags", []):
            return False
        if self.status and doc_meta.get("status") != self.status:
            return False
        return True
