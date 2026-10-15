"""Canonical retrieval filter contract."""

from __future__ import annotations

from _workspace_contract import WorkspaceMatch

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SearchFilters:
    """Optional artefact filters shared across retrieval entrypoints."""

    type: str | None = None
    tag: str | None = None
    status: str | None = None

    workspace: str | None = None
    workspace_match: WorkspaceMatch = WorkspaceMatch.RELEVANT

    def __post_init__(self):
        from _common._workspace import validate_workspace_query

        validate_workspace_query(self.workspace, self.workspace_match)

    def matches(self, doc_meta: Mapping[str, Any]) -> bool:
        """Return whether one document or result metadata dict matches."""
        if self.type and doc_meta.get("type") != self.type:
            return False
        if self.tag and self.tag not in doc_meta.get("tags", []):
            return False
        if self.status and doc_meta.get("status") != self.status:
            return False
        from _common._workspace import matches_workspace_query

        return matches_workspace_query(doc_meta, self.workspace, self.workspace_match)
