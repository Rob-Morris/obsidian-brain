"""Supported value types used to construct public command requests."""

from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.access.reduce import CommandReduction, LeaseReduction, ResetReduction
from _application.artefact.list import ArtefactListLocation, ArtefactSort
from _application.artefact.read import ArtefactLocation
from _application.artefact.repair import ArtefactRepairScope
from _application.artefact.reparent_children import ReparentChildrenMode
from _application.artefact.search import ArtefactSearchMode
from _application.content.classify import ContentClassifyMode
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.edit import (
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
from _application.document.patch import AllMatches, OccurrenceMatch, UniqueMatch
from _application.document.write import DocumentWriteOperation
from _application.requests import CatalogueCursor
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
    "CheckSeverity",
    "CommandOwner",
    "CommandReduction",
    "ContentClassifyMode",
    "DeleteStructure",
    "DependencyTier",
    "DocumentIntroSelection",
    "DocumentLocator",
    "DocumentResource",
    "DocumentWriteOperation",
    "EffectClass",
    "FrontmatterField",
    "HeadingAncestor",
    "HeadingBlockSelection",
    "HeadingPart",
    "HeadingSelection",
    "InlineContent",
    "InsertPosition",
    "InsertStructure",
    "LeaseReduction",
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
    "ResetReduction",
    "RetryClass",
    "SearchableResource",
    "ShapingMode",
    "SkillCreateTarget",
    "StagedContent",
    "StyleCreateTarget",
    "TemplateCreateTarget",
    "UniqueMatch",
    "WorkspaceMetadataLink",
)
