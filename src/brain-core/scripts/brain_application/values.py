"""Supported value types used to construct public command requests."""

from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.access.reduce import ClearGrants, DiscardOperations, NarrowInitial, RevokeGrants
from _application.access.request import CommandConsent, OperationConsent
from _application.access.prepare import InspectOperation, PrepareCommand
from _application.access_contracts import AccessPageCursor, AccessStatusView
from _application.artefact.list import ArtefactListLocation, ArtefactSort
from _application.artefact.read import ArtefactLocation
from _application.artefact.repair import ArtefactRepairScope
from _application.artefact.reparent_children import ReparentChildrenMode
from _application.artefact.search import ArtefactSearchMode
from _application.content.classify import ContentClassifyMode
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.structured_edit import (
    CalloutAncestor,
    CalloutBlockSelection,
    CalloutPart,
    CalloutSelection,
    DeleteStructure,
    DocumentIntroSelection,
    HeadingAncestor,
    HeadingBlockSelection,
    HeadingPart,
    HeadingSelection,
    InsertPosition,
    InsertStructure,
    ReplaceStructure,
)
from _application.document.replace_text import AllMatches, OccurrenceMatch, UniqueMatch
from _application.document.write_body import DocumentWriteBodyOperation
from _application.requests import CatalogueCursor, CommandListView
from _application._response_budget import TextCursor
from _application.resource.create import (
    MemoryCreateTarget,
    SkillCreateTarget,
    StyleCreateTarget,
    TemplateCreateTarget,
)
from _application.resource.list import ListableResource
from _application.resource.read import ReadableResource
from _application.resource.search import SearchableResource
from _application.shaping.render import PresentationOutput, PrintableOutput
from _application.shaping.start import ShapingMode
from _application.type._classification import ArtefactTypeClassification
from _application.types import (
    Authority,
    Availability,
    CommandOwner,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    RetryClass,
)
from _application.vault.check import CheckSeverity
from _application.workspace.update_metadata import WorkspaceMetadataLink
from _application.workspace_context import WorkspaceSelector

__all__ = (
    "AllMatches",
    "ArtefactListLocation",
    "ArtefactLocation",
    "ArtefactRepairScope",
    "ArtefactSearchMode",
    "ArtefactSort",
    "ArtefactTypeClassification",
    "Authority",
    "Availability",
    "CalloutAncestor",
    "CalloutBlockSelection",
    "CalloutPart",
    "CalloutSelection",
    "CatalogueCursor",
    "TextCursor",
    "CommandListView",
    "CheckSeverity",
    "CommandOwner",
    "CommandConsent",
    "OperationConsent",
    "PrepareCommand",
    "InspectOperation",
    "AccessPageCursor",
    "AccessStatusView",
    "ContentClassifyMode",
    "DeleteStructure",
    "DependencyTier",
    "DocumentIntroSelection",
    "DocumentLocator",
    "DocumentResource",
    "DocumentWriteBodyOperation",
    "EffectClass",
    "FrontmatterField",
    "HeadingAncestor",
    "HeadingBlockSelection",
    "HeadingPart",
    "HeadingSelection",
    "InlineContent",
    "InsertPosition",
    "InsertStructure",
    "ClearGrants",
    "DiscardOperations",
    "NarrowInitial",
    "RevokeGrants",
    "ListableResource",
    "Locality",
    "MemoryCreateTarget",
    "OccurrenceMatch",
    "PresentationOutput",
    "PrintableOutput",
    "Projection",
    "ReadableResource",
    "ReparentChildrenMode",
    "ReplaceStructure",
    "RetryClass",
    "SearchableResource",
    "ShapingMode",
    "SkillCreateTarget",
    "StagedContent",
    "StyleCreateTarget",
    "TemplateCreateTarget",
    "UniqueMatch",
    "WorkspaceMetadataLink",
    "WorkspaceSelector",
)
