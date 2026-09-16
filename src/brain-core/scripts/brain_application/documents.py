"""Stable typed-Python contracts for editable Brain Markdown documents.

Request field semantics are generated from the authoritative catalogue schema;
this module exposes construction types without executor or registration hooks.
"""

from _application.document._types import DocumentLocator, DocumentResource
from _application.document.structured_edit import (
    CalloutAncestor,
    CalloutBlockSelection,
    CalloutPart,
    CalloutSelection,
    DeleteStructure,
    DocumentStructuredEditRequest,
    DocumentIntroSelection,
    HeadingAncestor,
    HeadingBlockSelection,
    HeadingPart,
    HeadingSelection,
    InsertPosition,
    InsertStructure,
    ReplaceStructure,
)
from _application.document.replace_text import (
    AllMatches,
    DocumentReplaceTextRequest,
    OccurrenceMatch,
    UniqueMatch,
)
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.write_body import DocumentWriteBodyOperation, DocumentWriteBodyRequest
from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.workspace_context import WorkspaceSelector

__all__ = (
    "AllMatches",
    "CalloutAncestor",
    "CalloutBlockSelection",
    "CalloutPart",
    "CalloutSelection",
    "DeleteStructure",
    "DocumentStructuredEditRequest",
    "DocumentIntroSelection",
    "DocumentLocator",
    "DocumentReplaceTextRequest",
    "DocumentResource",
    "DocumentUpdateFrontmatterRequest",
    "DocumentWriteBodyOperation",
    "DocumentWriteBodyRequest",
    "FrontmatterField",
    "HeadingAncestor",
    "HeadingBlockSelection",
    "HeadingPart",
    "HeadingSelection",
    "InsertPosition",
    "InsertStructure",
    "InlineContent",
    "OccurrenceMatch",
    "ReplaceStructure",
    "StagedContent",
    "UniqueMatch",
    "WorkspaceSelector",
)
