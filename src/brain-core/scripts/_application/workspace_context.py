"""Symbolic selection and immutable policy inputs for semantic artefact mutations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from functools import wraps
from typing import Annotated

from _workspace_contract import WorkspacePolicy
from .preparation import ObservedResource, content_digest
from .results import Partial
from ._decoding import OMITTED
from .relationship._types import Disposition


@dataclass(frozen=True, slots=True)
class WorkspaceSelector:
    value: str

    def __post_init__(self):
        from _common._slugs import validate_key
        if not isinstance(self.value, str):
            raise ValueError("workspace_context must be global or workspace/{key}, never a path")
        if self.value == "global":
            return
        if not self.value.startswith("workspace/"):
            raise ValueError("workspace_context must be global or workspace/{key}, never a path")
        validate_key(self.value.removeprefix("workspace/"))


@dataclass(frozen=True, slots=True)
class WorkspaceSelectorCodec:
    def schema(self):
        from _common._slugs import CANONICAL_KEY_PATTERN
        return {"anyOf": [{"type": "null"}, {"const": "global", "type": "string"},
                          {"type": "string", "pattern": "^workspace/" + CANONICAL_KEY_PATTERN.removeprefix("^")}]}

    def encode(self, value):
        return value.value if value is not None else None

    def decode(self, value):
        return WorkspaceSelector(value) if value is not None else None


WORKSPACE_SELECTOR_CODEC = WorkspaceSelectorCodec()
WorkspaceSelection = Annotated[WorkspaceSelector | None, WORKSPACE_SELECTOR_CODEC]


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceAwareRequest:
    workspace_context: WorkspaceSelection = field(default=None, metadata={
        "description": "Canonical workspace/{key} or global; omission uses validated startup context. Never a filesystem path.",
    })


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceDispositionRequest(WorkspaceAwareRequest):
    """Native mutation capability for revision-bound dependant withdrawal."""

    relationship_dispositions: tuple["Disposition", ...] = field(default=(), metadata={
        "description": "Explicit revision-bound withdrawal of semantic dependants affected by this native transition; native owners retain authority.",
    })


def workspace_request_decoder(decode):
    """Compose one selector decoder around each owner's remaining strict fields."""
    @wraps(decode)
    def combined(payload):
        fields = dict(payload)
        selection = WORKSPACE_SELECTOR_CODEC.decode(fields.pop("workspace_context", None))
        return replace(decode(fields), workspace_context=selection)
    return combined


def workspace_disposition_decoder(decode):
    """Add disposition decoding only for native owners which consume it."""
    @workspace_request_decoder
    @wraps(decode)
    def combined(payload):
        fields = dict(payload)
        from .relationship.batch import decode_disposition
        dispositions = tuple(decode_disposition(item) for item in fields.pop("relationship_dispositions", ()))
        return replace(decode(fields), relationship_dispositions=dispositions)
    return combined


def validate_workspace_request(request, *, resource="artefact"):
    from .semantic_mutations import SEMANTIC_ARTEFACT_COMMANDS
    if request.COMMAND_ID not in SEMANTIC_ARTEFACT_COMMANDS:
        raise ValueError("workspace_context requires a classified semantic artefact owner")
    selection = request.workspace_context
    if selection is not None and not isinstance(selection, WorkspaceSelector):
        raise ValueError("workspace_context must be a WorkspaceSelector")
    if resource != "artefact" and selection is not None:
        raise ValueError("workspace_context is available only for artefact documents")


@dataclass(frozen=True, slots=True)
class EffectiveMutationContext:
    workspace: str | None
    selection: str
    local_overrides: bool
    shared_policy: WorkspacePolicy
    local_policy: WorkspacePolicy
    parent: str | None
    parent_source: str
    tags: tuple[str, ...]
    sources: tuple[ObservedResource, ...]
    source_workspace: str | None = None
    selection_reason: str = "startup"
    relevant_workspaces: tuple[str, ...] = ()

    def review(self):
        return asdict(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceMutationPartial(Partial):
    """Known committed subjects retain the exact policy snapshot used to write them."""

    mutation_context: EffectiveMutationContext


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceMutationPayload:
    mutation_context: EffectiveMutationContext | None = None


def resolve_mutation_context(context, router, selector, *, parent=OMITTED, creation=False,
                             explicit_tags=(), workspace_identity=False):
    """Validate startup intent even for overrides, then resolve one policy snapshot."""
    from _bootstrap.workspace_binding import load_workspace_manifest_state, resolve_selected_workspace_binding, WorkspaceBindingError
    from _common import document_revision_at, resolve_parent_reference
    from _common._workspace import BindingState, membership, normalise_tags, require_workspace, workspace_policy
    import vault_registry
    from pathlib import Path

    sources = []
    manifest = None
    if (context.workspace_dir is not None
            and context.workspace_dir.resolve() != context.selected_brain.vault_root.resolve()):
        try:
            state = load_workspace_manifest_state(context.workspace_dir)
        except WorkspaceBindingError as exc:
            raise ValueError(f"Invalid workspace binding: {exc}; run brain workspace register") from exc
        manifest = state.data
        for path in (state.manifest_path, state.legacy_path):
            sources.append(ObservedResource("workspace-manifest", str(path),
                content_digest(path.read_bytes()) if path.exists() else None))
    state, bound_workspace, guidance, _cause = resolve_selected_workspace_binding(
        context.selected_brain.vault_root, router, manifest)
    if state not in {BindingState.VALID, BindingState.UNCONFIGURED}:
        raise ValueError(f"Workspace binding is {state.value}: {guidance}")
    if bound_workspace is not None:
        bound_hub = router["artefact_index"][bound_workspace]
        sources.append(ObservedResource("workspace-source", bound_workspace,
            document_revision_at(context.selected_brain.vault_root / bound_hub["path"])))
    if manifest is not None:
        registry = Path(vault_registry.registry_path())
        sources.append(ObservedResource("brain-binding", str(registry), content_digest(registry.read_bytes())))

    explicit_parent = parent is not OMITTED
    parent = None if parent is OMITTED else parent
    parent_entry = None
    if creation and parent is not None:
        parent, parent_entry = resolve_parent_reference(str(context.selected_brain.vault_root), router, parent)
    selected = selector.value if selector is not None else bound_workspace
    reason = "explicit" if selector is not None else "startup"
    if workspace_identity:
        if parent is not None:
            raise ValueError("Workspace records must have no parent; configure default_parent for their content instead")
        if selector is not None and selector.value != "global":
            raise ValueError("Workspace identities are self-scoped; they cannot belong to another workspace")
        selected, reason = None, "identity"
    elif creation and selector is None and parent_entry is not None:
        selected, reason = membership(parent, parent_entry), "parent"
    workspace = None if selected == "global" else selected
    local_applies = workspace is not None and workspace == bound_workspace
    shared = WorkspacePolicy()
    local = WorkspacePolicy()
    if workspace is not None:
        hub = require_workspace(router, workspace, active=True)
        shared = workspace_policy(router, workspace, hub)
        sources.append(ObservedResource("workspace-policy", workspace,
            document_revision_at(context.selected_brain.vault_root / hub["path"])))
        if local_applies:
            local = workspace_policy(router, workspace, manifest.get("defaults", {}), local=True)
    parent_source = ("explicit" if creation else "preserved") if explicit_parent else "none"
    if creation and not explicit_parent and not workspace_identity:
        parent = local.parent or shared.parent
        parent_source = "local" if local.parent else "shared" if shared.parent else "none"
    if parent is not None:
        if parent_entry is None:
            parent, parent_entry = resolve_parent_reference(str(context.selected_brain.vault_root), router, parent)
        if creation and membership(parent, parent_entry) != workspace:
            destination = membership(parent, parent_entry) or "global"
            raise ValueError(f"Parent {parent} and created artefact must share one workspace; "
                             f"select workspace_context={destination} or supply a parent in {workspace or 'global'}")
        sources.append(ObservedResource("workspace-parent", parent,
            document_revision_at(context.selected_brain.vault_root / parent_entry["path"])))
    tags = tuple(dict.fromkeys(shared.tags + local.tags + normalise_tags(explicit_tags)))
    if creation and not workspace_identity and bound_workspace is not None and workspace != bound_workspace:
        tags = tuple(dict.fromkeys((*tags, bound_workspace)))
    return EffectiveMutationContext(workspace, selector.value if selector else "startup", local_applies,
        shared, local, parent, parent_source, tags, tuple(sources), bound_workspace, reason,
        tuple(tag for tag in tags if tag.startswith("workspace/")))


def apply_semantic_tags(fields, effective):
    """Apply policy to an intentional surviving subject, never to incidental rewrites."""
    from _common._workspace import normalise_tags
    updated = dict(fields)
    if effective.tags:
        updated["tags"] = list(dict.fromkeys(effective.tags + normalise_tags(fields.get("tags", []))))
    return updated


def validate_subject_membership(router, fields, reference, original_fields):
    """Preserve the subject's own historical membership independently of selected policy."""
    from _common._workspace import membership, require_workspace, validate_ownership
    if original_fields.get("type") == "living/workspace" and fields.get("type") != "living/workspace":
        raise ValueError("Semantic document edits must preserve workspace hub type")
    workspace = membership(reference, fields)
    if workspace != membership(reference, original_fields):
        raise ValueError("Semantic document edits must preserve workspace membership")
    if workspace is not None:
        require_workspace(router, workspace)
    validate_ownership(reference, fields, router.get("artefact_index", {}))
