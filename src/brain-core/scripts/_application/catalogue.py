"""Static selected-Brain command catalogue contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Callable, get_args, get_origin

from .context import InvocationContext
from .identity import command_identity
from .results import CommandResult, RESULT_SCHEMA
from .types import (
    Authority,
    CommandLifecycle,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
    validate_command_id,
)


CATALOGUE_SCHEMA = "brain.command-catalogue/1"
APPLICATION_PROJECTIONS = (
    Projection.MCP,
    Projection.CLI,
    Projection.SCRIPT,
    Projection.PYTHON,
)
ALL_APPLICATION_PROJECTIONS = tuple(
    ProjectionEligibility(projection, True)
    for projection in APPLICATION_PROJECTIONS
)
Executor = Callable[[InvocationContext, object], CommandResult]


@dataclass(frozen=True, slots=True)
class ApplicationEntry:
    request_type: type
    executor: Executor
    dependency_tier: DependencyTier
    locality: Locality
    required_providers: tuple[str, ...]
    optional_providers: tuple[str, ...]
    authority: Authority
    effect_class: EffectClass
    retry_class: RetryClass
    projections: tuple[ProjectionEligibility, ...]
    summary: str = ""
    open_world: bool = False
    lifecycle: CommandLifecycle = CommandLifecycle.ACTIVE
    replacement_command_id: str | None = None

    @property
    def command_id(self) -> str:
        return self.request_type.COMMAND_ID

    @property
    def command_version(self) -> int:
        return self.request_type.COMMAND_VERSION

    @property
    def result_type(self):
        return self.request_type.RESULT_TYPE

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1:
            raise ValueError("application entry command version must be positive")
        if self.locality is Locality.MACHINE_LOCAL:
            raise ValueError("machine-local commands belong to the launcher manifest")
        required = self.required_providers
        optional = self.optional_providers
        if any(not item.strip() for item in (*required, *optional)):
            raise ValueError("provider names must be non-empty")
        if len(required) != len(set(required)) or len(optional) != len(set(optional)):
            raise ValueError("provider names must be unique within each binding class")
        if set(required) & set(optional):
            raise ValueError("a provider cannot be both required and optional")
        if required != tuple(sorted(required)) or optional != tuple(sorted(optional)):
            raise ValueError("provider names must use deterministic sorted order")
        projection_names = [projection.projection for projection in self.projections]
        if tuple(projection_names) != APPLICATION_PROJECTIONS:
            raise ValueError(
                "application entries must describe MCP, CLI, script and Python in canonical order"
            )
        if not self.summary:
            object.__setattr__(self, "summary", _summary(self.command_id))
        if not self.summary.strip() or not self.summary.endswith("."):
            raise ValueError("application entry summary must be one non-empty sentence")
        if not isinstance(self.open_world, bool):
            raise ValueError("application entry open_world must be a boolean")
        if not isinstance(self.lifecycle, CommandLifecycle):
            raise ValueError("application entry lifecycle must be closed and typed")
        if self.lifecycle is CommandLifecycle.REPLACED:
            if self.replacement_command_id is None:
                raise ValueError("replaced application entry requires replacement guidance")
            validate_command_id(self.replacement_command_id)
        elif self.replacement_command_id is not None:
            raise ValueError("only a replaced application entry may name a replacement")

    @property
    def eligible_projections(self) -> tuple[Projection, ...]:
        return tuple(
            projection.projection
            for projection in self.projections
            if projection.supported
        )


@dataclass(frozen=True, slots=True)
class ApplicationCatalogue:
    entries: tuple[ApplicationEntry, ...]
    schema: str = CATALOGUE_SCHEMA
    result_schema: str = RESULT_SCHEMA
    interface_epoch: int = 2

    def __post_init__(self) -> None:
        if self.schema != CATALOGUE_SCHEMA:
            raise ValueError(f"unsupported application catalogue schema: {self.schema}")
        if self.result_schema != RESULT_SCHEMA:
            raise ValueError(f"unsupported command result schema: {self.result_schema}")
        if self.interface_epoch < 1:
            raise ValueError("application catalogue interface epoch must be positive")
        ids = [entry.command_id for entry in self.entries]
        request_types = [entry.request_type for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("application catalogue command identifiers must be unique")
        if len(request_types) != len(set(request_types)):
            raise ValueError("application catalogue request types must be unique")
        if ids != sorted(ids):
            raise ValueError("application catalogue entries must be sorted by command_id")

    def resolve(self, request: object) -> ApplicationEntry:
        command_id, version, result_type = command_identity(request)
        entry = next(
            (item for item in self.entries if item.request_type is type(request)),
            None,
        )
        if entry is None:
            raise KeyError(f"request is not present in application catalogue: {command_id}")
        if (entry.command_id, entry.command_version, entry.result_type) != (
            command_id,
            version,
            result_type,
        ):
            raise RuntimeError(f"request/catalogue identity mismatch for {command_id}")
        return entry

    @property
    def fingerprint(self) -> str:
        payload = {
            "schema": self.schema,
            "result_schema": self.result_schema,
            "interface_epoch": self.interface_epoch,
            "entries": [
                {
                    "command_id": entry.command_id,
                    "command_version": entry.command_version,
                    "request_type": (
                        f"{entry.request_type.__module__}:{entry.request_type.__qualname__}"
                    ),
                    "result_type": type_identity(entry.result_type),
                    "dependency_tier": entry.dependency_tier.name.lower(),
                    "locality": entry.locality.value,
                    "required_providers": entry.required_providers,
                    "optional_providers": entry.optional_providers,
                    "authority": entry.authority.value,
                    "effect_class": entry.effect_class.value,
                    "retry_class": entry.retry_class.value,
                    "summary": entry.summary,
                    "open_world": entry.open_world,
                    "lifecycle": entry.lifecycle.value,
                    "replacement_command_id": entry.replacement_command_id,
                    "projections": tuple(
                        (item.projection.value, item.supported, item.reason)
                        for item in entry.projections
                    ),
                }
                for entry in self.entries
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def exclude_projection(
    entry: ApplicationEntry,
    projection: Projection,
    reason: str,
) -> ApplicationEntry:
    """Return an entry with one deliberately unsupported public projection."""
    if not isinstance(projection, Projection):
        raise ValueError("projection exclusion requires a typed projection")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("projection exclusion requires a non-empty reason")
    projections = tuple(
        ProjectionEligibility(item.projection, False, reason.strip())
        if item.projection is projection
        else item
        for item in entry.projections
    )
    if projections == entry.projections:
        raise ValueError(f"projection is already unsupported: {projection.value}")
    return replace(entry, projections=projections)


def type_identity(annotation: object) -> str:
    """Return a stable identity for a payload type or strict payload union."""
    if get_origin(annotation) is not None:
        arguments = get_args(annotation)
        if not arguments:
            raise TypeError(f"unsupported result annotation: {annotation!r}")
        return "union[" + ",".join(type_identity(item) for item in arguments) + "]"
    module = getattr(annotation, "__module__", None)
    qualname = getattr(annotation, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        raise TypeError(f"unsupported result annotation: {annotation!r}")
    return f"{module}:{qualname}"


def _summary(command_id: str) -> str:
    descriptions = {'command.list': 'Discover commands with concise summaries, access state and '
                     'pagination; use command.describe for schemas.',
     'command.describe': 'Get the complete input and result schemas, permissions and '
                         'examples for one command.',
     'session.start': 'Load Brain instructions, preferences and discovery routes before '
                      'doing vault work.',
     'artefact.read': 'Read an active or archived note by path or reference, including its '
                      'revision for safe edits.',
     'artefact.create': 'Create a typed note from inline content or a staged source using '
                        'its configured naming and frontmatter.',
     'artefact.list': 'Browse active or archived notes with filters and stable pagination.',
     'artefact.search': 'Find notes by lexical text search with optional semantic '
                        'retrieval.',
     'artefact.archive': 'Move a note to the archive while preserving its content and '
                         'identity.',
     'artefact.unarchive': 'Restore an archived note to the active vault.',
     'artefact.delete': 'Permanently remove a note through an administrator-authorized '
                        'command.',
     'artefact.outline': 'Read a note heading outline to target a structural edit.',
     'artefact.rename': 'Rename a note and repair links that point to it.',
     'document.replace-text': 'Replace a selected text match in a document using its '
                              'expected revision.',
     'document.structured-edit': 'Edit a heading section or other structural target using '
                                 'its expected revision.',
     'document.update-frontmatter': 'Update document frontmatter fields using its expected '
                                    'revision.',
     'document.write-body': 'Replace a document body while retaining frontmatter and '
                            'checking its expected revision.',
     'invocation.read': 'Look up the recorded outcome of an interrupted mutation by '
                        'invocation ID before retrying.',
     'runtime.refresh-router': 'Recompile the vault router after configuration or artefact '
                               'metadata changes.',
     'retrieval.refresh-lexical': 'Refresh the lexical search index after note changes or '
                                  'external file removal.',
     'vault.read-file': 'Read a vault-relative file, including Brain core documentation '
                        'and configuration sources.',
     'vault.read-router': 'Read compiled router metadata, always rules and source digests.',
     'vault.read-config': 'Read the selected Brain configuration through the filtered '
                          'command surface.',
     'shaping.start': 'Load the version-matched workflow for shaping an artefact through '
                      'questions and answers.',
     'links.check': 'Find broken or inconsistent links in a note or vault scope.',
     'links.fix': 'Repair supported broken or inconsistent links in a note or vault scope.'}
    if command_id in descriptions:
        return descriptions[command_id]
    noun, verb = command_id.split(".", 1)
    noun_words = noun.replace("-", " ")
    verb_words = verb.replace("-", " ")
    direct = {
        "create": "Create one {noun}",
        "read": "Read one {noun}",
        "list": "List {noun} resources",
        "search": "Search {noun} resources",
        "delete": "Delete one {noun}",
        "append": "Append content to one {noun}",
        "prepend": "Prepend content to one {noun}",
        "edit": "Edit one {noun}",
        "check": "Check {noun} state",
        "fix": "Fix {noun} state",
        "resolve": "Resolve {noun} state",
        "start": "Start {noun}",
        "upload": "Upload one {noun}",
        "discard": "Discard one {noun}",
    }
    if verb in direct:
        return direct[verb].format(noun=noun_words) + "."
    return f"{verb_words.capitalize()} for {noun_words}."
