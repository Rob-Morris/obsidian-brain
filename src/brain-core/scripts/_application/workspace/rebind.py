"""Explicitly retarget one folder while preserving both canonical workspaces."""

from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Mapping

from .._caller_workspace import compound_workspace_entry, require_string, workspace_dir
from .._decoding import reject_unexpected
from ..preparation import bind_operation
from ..receipts import CommittedEffect
from ..results import Error, Ok
from ..types import validate_slug
from .create import registration_error
from ._registration import require_registration, observe_path_claims, unique_observations
from ._preparation import _observe_file
from .register import WorkspaceBindingOutcome, WorkspaceRegisterPayload, stage_manifest


@dataclass(frozen=True, slots=True)
class WorkspaceRebindRequest:
    COMMAND_ID: ClassVar[str] = "workspace.rebind"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceRegisterPayload
    MINIMAL_EXAMPLE: ClassVar[dict] = {"brain_id": "brain", "slug": "example"}
    FIELD_DESCRIPTIONS: ClassVar[dict] = {
        "brain_id": "Registered identity of the selected target Brain.",
        "slug": "Existing active canonical workspace key in the target Brain.",
    }

    brain_id: str
    slug: str

    def __post_init__(self):
        require_string(self.brain_id, "brain_id")
        validate_slug(self.slug)


def source_root(context):
    from _bootstrap.workspace_binding import require_workspace_binding, resolve_local_brain_vault

    if context.workspace_dir is None or not context.workspace_dir.is_dir():
        raise ValueError("Rebind requires an existing caller folder")
    source = require_workspace_binding(context.workspace_dir)
    root = resolve_local_brain_vault(source["brain"])
    if root is None:
        raise ValueError("The source Brain cannot be resolved; repair its local registration before rebind")
    return root, source


def plan_rebind(context, request, *, frozen_inputs=None):
    import workspace_registry
    import vault_registry
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.workspace_binding import plan_workspace_binding, resolve_local_brain_vault
    from _bootstrap.mcp_registration import require_no_registered_integrations
    from _common._workspace import workspace_policy

    target = context.workspace_dir
    old_root, source = source_root(context)
    new_root = context.selected_brain.vault_root.resolve()
    if resolve_local_brain_vault(request.brain_id) != new_root:
        raise ValueError("Rebind brain_id must identify the selected target Brain")
    old_context = replace(context, selected_brain=replace(context.selected_brain,
                          brain_id=source["brain"], vault_root=old_root))
    _, _, observations = require_registration(old_context, source["slug"])
    router, registration, target_observations = require_registration(context, request.slug)
    observations.extend(target_observations)
    observations.extend(observe_path_claims(new_root, target, allowed_roots=(old_root,)))
    state, manifest = plan_workspace_binding(target, brain=request.brain_id,
                                             slug=request.slug, allow_rebind=True)
    workspace_policy(router, "workspace/" + request.slug, manifest.get("defaults", {}), local=True)
    plan = FilePlan()
    plan.observe_directory(target)
    files = {state.manifest_path, state.legacy_path, Path(vault_registry.registry_path())}
    path = workspace_registry.canonical_path(target)
    for root in sorted({old_root, new_root}, key=str):
        require_no_registered_integrations(root, target)
        rows, expected = workspace_registry.read_registry_strict(root)
        current = rows.get(path)
        if current is not None and current["slug"] != source["slug"]:
            raise ValueError(f"Conflicting registration for {target} in {root}; repair it before rebind")
        updated = dict(rows)
        if root == old_root:
            updated.pop(path, None)
        if root == new_root:
            updated[path] = {"slug": request.slug, "path": path}
        registry_path = Path(workspace_registry._registry_path(root))
        files.add(registry_path)
        if plan.read_bytes(registry_path) != expected:
            raise ValueError("Workspace registry changed during rebind planning")
        plan.write_text(registry_path, workspace_registry.registry_bytes(updated).decode("utf-8"))
    stage_manifest(plan, state, manifest)
    observations.extend(_observe_file(file) for file in sorted(files))
    stat = target.stat()
    from ..preparation import ObservedResource
    observations.append(ObservedResource("caller-workspace", str(target.resolve()), f"{stat.st_dev}:{stat.st_ino}"))
    binding = bind_operation(request, observations=unique_observations(observations), frozen_inputs=frozen_inputs,
                            review={"source": source, "target": {"brain": request.brain_id, "slug": request.slug},
                                    "folder": str(target), "files": [str(change.path) for change in plan.changes()]})
    return old_root, registration, manifest, plan, binding


def prepare(context, request, *, frozen_inputs=None):
    return plan_rebind(context, request, frozen_inputs=frozen_inputs)[4]


def execute(context, request):
    from _common import vault_mutation_lock, MutationLockError
    from _bootstrap.file_transaction import apply_file_changes, FileTransactionError
    from _bootstrap.workspace_binding import WorkspaceBindingError
    from _bootstrap.mcp_registration import registration_lock, require_no_registered_integrations

    target = workspace_dir(context, type(request))
    if isinstance(target, Error):
        return target
    effects = []
    try:
        old_root, _ = source_root(context)
        with ExitStack() as locks:
            locks.enter_context(registration_lock(Path.home()))
            for root in sorted({old_root, context.selected_brain.vault_root.resolve()}, key=str):
                locks.enter_context(vault_mutation_lock(root))
            locks.enter_context(vault_mutation_lock(target))
            observed_root, registration, manifest, plan, binding = plan_rebind(
                context, request, frozen_inputs=context.admission.frozen_inputs)
            if observed_root != old_root:
                raise ValueError("Source Brain changed before the folder lock; retry rebind")
            context.admission.admit(binding)
            changes = plan.changes()
            def before_write():
                plan.validate_dependencies()
                claims = observe_path_claims(context.selected_brain.vault_root, target, allowed_roots=(old_root,))
                if any(item not in binding.observations for item in claims):
                    raise ValueError("Machine workspace inventory changed after admission; retry rebind")
                for root in {old_root, context.selected_brain.vault_root.resolve()}:
                    require_no_registered_integrations(root, target)
            plan.validate()
            if not context.dry_run:
                try:
                    apply_file_changes(changes, before_write=before_write)
                except FileTransactionError as exc:
                    effects.extend(CommittedEffect("workspace.rebound", str(path)) for path in exc.surviving_paths)
                    raise
                effects.extend(CommittedEffect("workspace.rebound", str(change.path)) for change in changes)
    except (OSError, ValueError, WorkspaceBindingError, MutationLockError, FileTransactionError) as exc:
        if effects:
            import shlex
            recovery = f"brain workspace upgrade --workspace {shlex.quote(str(target))}"
            exc = ValueError(f"{exc}. The folder manifest remains authoritative; review and repair its selected "
                             f"registration with {recovery}; explicitly repair stale rows in the other Brain.")
        return registration_error(request, exc, effects)
    status = "planned" if context.dry_run else "changed" if effects else "noop"
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
              WorkspaceRegisterPayload(replace(registration, status="planned") if context.dry_run else registration,
                  WorkspaceBindingOutcome(manifest["brain"], manifest["slug"], manifest["slug"], status), context.dry_run),
              committed_effects=tuple(effects))


def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {"brain_id", "slug"})
    return WorkspaceRebindRequest(require_string(payload.get("brain_id"), "brain_id"),
                                  require_string(payload.get("slug"), "slug"))


def catalogue_entry():
    return compound_workspace_entry(WorkspaceRebindRequest, execute)
