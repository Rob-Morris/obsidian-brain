"""Stable typed-Python contracts for editable Brain Markdown documents.

Request field semantics are generated from the authoritative catalogue schema;
this module exposes construction types without executor or registration hooks.
"""

from _application.document._types import DocumentLocator, DocumentResource
from _application.document.edit import (
    CalloutAncestor,
    CalloutBlockSelection,
    CalloutPart,
    CalloutSelection,
    DeleteStructure,
    DocumentEditRequest,
    DocumentIntroSelection,
    HeadingAncestor,
    HeadingBlockSelection,
    HeadingPart,
    HeadingSelection,
    InsertPosition,
    InsertStructure,
    ReplaceStructure,
)
from _application.document.patch import (
    AllMatches,
    DocumentPatchRequest,
    OccurrenceMatch,
    UniqueMatch,
)
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.write import DocumentWriteOperation, DocumentWriteRequest
from _application._mutation_support import FrontmatterField, InlineContent, StagedContent

__all__ = (
    "AllMatches",
    "CalloutAncestor",
    "CalloutBlockSelection",
    "CalloutPart",
    "CalloutSelection",
    "DeleteStructure",
    "DocumentEditRequest",
    "DocumentIntroSelection",
    "DocumentLocator",
    "DocumentPatchRequest",
    "DocumentResource",
    "DocumentUpdateFrontmatterRequest",
    "DocumentWriteOperation",
    "DocumentWriteRequest",
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
)
