"""Validated shared mutation policy owned by a Brain workspace hub."""

from dataclasses import dataclass, replace
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._mutation_support import contributor_mutation_entry
from ..preparation import ObservedResource, OperationPreparation, bind_operation, content_digest, canonical_json
from ..receipts import CommittedEffect
from ..results import Ok
from .ensure_registration import registration_error


@dataclass(frozen=True, slots=True)
class WorkspacePolicyPayload:
    workspace: str
    default_parent: str | None
    default_tags: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class WorkspaceUpdatePolicyRequest:
    COMMAND_ID: ClassVar[str] = "workspace.update-policy"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspacePolicyPayload
    MINIMAL_EXAMPLE: ClassVar[dict] = {"workspace": "workspace/example", "clear_parent": True}

    workspace: str
    default_parent: str | None = None
    clear_parent: bool = False
    default_tags: tuple[str, ...] | None = None

    def __post_init__(self):
        from _common._workspace import workspace_reference, normalise_tags
        workspace_reference(self.workspace)
        if not isinstance(self.clear_parent, bool):
            raise ValueError("clear_parent must be a boolean")
        if self.default_parent is not None:
            from .._caller_workspace import require_string
            require_string(self.default_parent, "default_parent")
            if self.clear_parent:
                raise ValueError("default_parent and clear_parent are mutually exclusive")
        if self.default_tags is not None:
            normalise_tags(self.default_tags)
        if self.default_parent is None and not self.clear_parent and self.default_tags is None:
            raise ValueError("workspace.update-policy requires at least one change")


def plan_policy(context, request):
    from _common._workspace import workspace_reference, normalise_tags, require_workspace, workspace_policy
    from _common import parse_frontmatter, serialize_frontmatter
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    router = require_fresh_compiled_router(str(context.selected_brain.vault_root))
    reference = workspace_reference(request.workspace)
    entry = require_workspace(router, reference, active=True)
    path = context.selected_brain.vault_root / entry["path"]
    if path.is_symlink():
        raise ValueError("Workspace policy refuses a symbolic-link target")
    raw = path.read_bytes()
    fields, body = parse_frontmatter(raw.decode("utf-8"))
    if request.clear_parent:
        fields.pop("default_parent", None)
    elif request.default_parent is not None:
        fields["default_parent"] = request.default_parent
    if request.default_tags is not None:
        fields["default_tags"] = list(normalise_tags(request.default_tags))
    policy = workspace_policy(router, reference, fields)
    if policy.parent is not None:
        fields["default_parent"] = policy.parent
    observations = [ObservedResource("workspace-file", str(path), content_digest(raw)),
                    ObservedResource("workspace-index", reference, content_digest(canonical_json(router["artefact_index"])))]
    if policy.parent:
        parent_path = context.selected_brain.vault_root / router["artefact_index"][policy.parent]["path"]
        if parent_path != path:
            observations.append(ObservedResource("workspace-file", str(parent_path), content_digest(parent_path.read_bytes())))
    binding = bind_operation(request, observations=observations,
        review={"workspace": reference, "default_parent": policy.parent, "default_tags": list(policy.tags)})
    return path, fields, body, policy, binding, serialize_frontmatter(fields, body=body) != raw.decode("utf-8")


def prepare(context, request, *, frozen_inputs=None):
    return plan_policy(context, request)[4]


def execute(context, request):
    from _common._workspace import workspace_reference
    from _common import vault_mutation_lock, MutationLockError, safe_write_artefact, serialize_frontmatter
    from .._transition_indexes import reconcile_transition_indexes, TransitionIndexesIncomplete

    effects = []
    try:
        with vault_mutation_lock(context.selected_brain.vault_root):
            path, fields, body, policy, binding, changed = plan_policy(context, request)
            context.admission.admit(binding)
            if changed and not context.dry_run:
                fields["modified"] = context.clock.now().isoformat()
                safe_write_artefact(path, serialize_frontmatter(fields, body=body), bounds=context.selected_brain.vault_root)
                effects.append(CommittedEffect("workspace.policy-updated", str(path.relative_to(context.selected_brain.vault_root))))
                reconcile_transition_indexes(context)
    except (OSError, ValueError, MutationLockError, TransitionIndexesIncomplete) as exc:
        return registration_error(request, exc, effects)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, WorkspacePolicyPayload(
        workspace_reference(request.workspace), policy.parent, policy.tags,
        "planned" if context.dry_run else "changed" if changed else "noop"), committed_effects=tuple(effects))


def decode(payload: Mapping[str, object]):
    from _common._workspace import normalise_tags
    reject_unexpected(payload, {"workspace", "default_parent", "clear_parent", "default_tags"})
    tags = payload.get("default_tags")
    if tags is not None:
        tags = normalise_tags(tags)
    return WorkspaceUpdatePolicyRequest(payload.get("workspace"), payload.get("default_parent"),
        payload.get("clear_parent", False), tags)


def catalogue_entry():
    return replace(contributor_mutation_entry(WorkspaceUpdatePolicyRequest, execute),
                   preparation=OperationPreparation(prepare))
