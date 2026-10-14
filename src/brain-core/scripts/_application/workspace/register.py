"""One admitted Brain-first registration operation across two separately locked boundaries."""

from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Mapping

from .._caller_workspace import compound_workspace_entry, require_string, workspace_dir
from .._decoding import reject_unexpected
from ..preparation import bind_operation, ObservedResource, canonical_json, content_digest
from ..receipts import CommittedEffect
from ..results import Error, Ok
from ..types import validate_slug
from ._registration import WorkspaceRegistration, require_registration, observe_path_claims, unique_observations
from .create import registration_error


@dataclass(frozen=True, slots=True)
class WorkspaceBindingOutcome:
    brain: str
    slug: str
    workspace: str
    status: str


@dataclass(frozen=True, slots=True)
class WorkspaceRegisterPayload:
    registration: WorkspaceRegistration
    binding: WorkspaceBindingOutcome
    dry_run: bool


@dataclass(frozen=True, slots=True)
class WorkspaceRegisterRequest:
    COMMAND_ID: ClassVar[str] = "workspace.register"
    COMMAND_VERSION: ClassVar[int] = 4
    RESULT_TYPE: ClassVar[type] = WorkspaceRegisterPayload
    FIELD_DESCRIPTIONS: ClassVar[dict] = {
        "brain_id": "Registered local Brain identity; must identify the selected Brain.",
        "slug": "Canonical workspace key; required for first registration, optional for a validated repeat.",
    }

    brain_id: str | None = None
    slug: str | None = None

    def __post_init__(self):
        require_string(self.brain_id, "brain_id", optional=True)
        require_string(self.slug, "slug", optional=True)
        if self.slug is not None:
            validate_slug(self.slug)


def _observe_scaffold(target):
    from _bootstrap.workspace_scaffold import resolve_brain_ignore_scaffold

    scaffold = resolve_brain_ignore_scaffold(target)
    revision = content_digest(canonical_json({
        "repo_root": str(scaffold.repo_root) if scaffold.repo_root else None,
        "git_dir": str(scaffold.git_dir) if scaffold.git_dir else None,
        "destination": str(scaffold.destination) if scaffold.destination else None,
    }))
    return scaffold, ObservedResource("caller-scaffold", str(target), revision)


def plan_register(context, request, *, frozen_inputs=None):
    import vault_registry
    import workspace_registry
    from _bootstrap.workspace_binding import (plan_workspace_binding, resolve_local_brain_alias,
                                              resolve_local_brain_vault, unregistered_brain_message)
    from _common._workspace import workspace_policy
    from ._preparation import _observe_file

    root, target = context.selected_brain.vault_root, context.workspace_dir
    if target is None or not target.is_dir():
        raise ValueError("workspace registration requires an existing caller directory")
    alias = request.brain_id
    if alias is None:
        alias = resolve_local_brain_alias(root)
        if alias is None:
            raise ValueError(unregistered_brain_message(root))
    selected = resolve_local_brain_vault(alias)
    if selected != root.resolve():
        raise ValueError("Registration brain_id must identify the selected Brain; select that Brain before registration")
    state, manifest = plan_workspace_binding(target, brain=alias, slug=request.slug,
                                             allow_rebind=False)
    reference = "workspace/" + manifest["slug"]
    key = reference.split("/", 1)[1]
    router, registration, observations = require_registration(context, key)
    workspace_policy(router, reference, manifest.get("defaults", {}), local=True)
    registry = workspace_registry.load_registry_strict(root)
    row = registry.get(workspace_registry.canonical_path(target))
    if row is not None and row["slug"] != key:
        raise ValueError("The selected folder has a conflicting registration; use workspace rebind or repair its stale row explicitly")
    files = {state.manifest_path, state.legacy_path, Path(vault_registry.registry_path()),
             Path(workspace_registry._registry_path(root))}
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.workspace_recovery import stage_legacy_workspace_recovery
    recovery = stage_legacy_workspace_recovery(FilePlan(), state)
    if recovery is not None:
        files.add(recovery)
    observations.extend(observe_path_claims(root, target))
    scaffold, scaffold_observation = _observe_scaffold(target)
    observations.append(scaffold_observation)
    if scaffold.repo_root == target.resolve():
        files.add(target / ".gitignore")
    if scaffold.destination is not None:
        files.add(scaffold.destination)
    stat = target.stat()
    observations.append(ObservedResource("caller-workspace", str(target.resolve()), f"{stat.st_dev}:{stat.st_ino}"))
    observations.extend(_observe_file(path) for path in sorted(files))
    binding = bind_operation(request, observations=unique_observations(observations), frozen_inputs=frozen_inputs,
        review={"registration": reference, "path": registration.path,
                "binding": manifest, "workspace": str(target), "order": "Brain registration, then caller binding"})
    local_observations = tuple(_observe_file(path) for path in sorted(files)
                              if path == state.manifest_path or path == state.legacy_path or path == recovery or path.name in {".gitignore", "exclude"})
    return router, registration, state, manifest, registry, binding, local_observations


def prepare_register(context, request, *, frozen_inputs=None):
    return plan_register(context, request, frozen_inputs=frozen_inputs)[5]


def stage_manifest(plan, state, manifest):
    from _common._yaml import dump_mapping_text
    from _bootstrap.workspace_recovery import stage_legacy_workspace_recovery

    stage_legacy_workspace_recovery(plan, state)
    plan.observe_directory(state.target_dir)
    plan.read_bytes(state.legacy_path)
    plan.write_text(state.manifest_path, dump_mapping_text(manifest))
    if state.source_path == state.legacy_path:
        plan.delete(state.legacy_path)


def execute(context, request):
    from _common import vault_mutation_lock, MutationLockError
    from _bootstrap.workspace_binding import WorkspaceBindingError
    from _bootstrap.file_transaction import FilePlan, FileTransactionError, apply_file_changes
    from _bootstrap.workspace_scaffold import ensure_brain_ignore_rules, GitInspectionError
    from _bootstrap.mcp_registration import registration_lock
    from .._transition_indexes import TransitionIndexesIncomplete
    from ._preparation import _observe_file
    import workspace_registry

    target = workspace_dir(context, type(request))
    if isinstance(target, Error):
        return target
    effects = []
    try:
        # Lock order is vault, then folder, never the reverse. The vault lock is held from the row
        # write through the manifest write, so a registry repair, which drops a row only after a fresh
        # read under that lock, cannot drop the new row while the old manifest is in place; it is
        # released before the ignore rules, which the folder lock alone guards.
        with ExitStack() as folder_lock:
            with registration_lock(Path.home()), vault_mutation_lock(context.selected_brain.vault_root):
                router, registration, state, manifest, registry, binding, local = plan_register(
                    context, request, frozen_inputs=context.admission.frozen_inputs)
                context.admission.admit(binding)
                key = manifest["slug"]
                if context.dry_run:
                    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, WorkspaceRegisterPayload(
                        replace(registration, status="planned"),
                        WorkspaceBindingOutcome(manifest["brain"], manifest["slug"], key, "planned"), True))
                folder_lock.enter_context(vault_mutation_lock(target))
                # The held caller lock must recheck the admitted local observations.
                identity = next(item for item in binding.observations if item.kind == "caller-workspace")
                stat = target.stat()
                if identity.identity != str(target.resolve()) or identity.revision != f"{stat.st_dev}:{stat.st_ino}":
                    raise ValueError("Caller workspace identity changed after admission; retry registration")
                admitted_scaffold = next(item for item in binding.observations if item.kind == "caller-scaffold")
                def revalidate_scaffold():
                    if _observe_scaffold(target)[1] != admitted_scaffold:
                        raise ValueError("Caller scaffold destination changed after admission; retry registration")
                revalidate_scaffold()
                if any(_observe_file(Path(item.identity)) != item for item in local):
                    raise ValueError("Caller workspace changed after admission; retry registration")
                def revalidate_claims():
                    observed_claims = observe_path_claims(context.selected_brain.vault_root, target)
                    if any(item not in binding.observations for item in observed_claims):
                        raise ValueError("Machine workspace inventory changed after admission; retry registration")
                revalidate_claims()
                if not workspace_registry.row_records(registry.get(workspace_registry.canonical_path(target)), target):
                    workspace_registry.register_workspace(context.selected_brain.vault_root, key, target)
                    effects.append(CommittedEffect("workspace.path-registered", f"selected-brain:.brain/local/workspaces.json#{target}"))
                manifest_plan = FilePlan()
                stage_manifest(manifest_plan, state, manifest)
                changes = manifest_plan.changes()
                manifest_plan.validate()
                def before_manifest_write():
                    manifest_plan.validate_dependencies()
                    revalidate_claims()
                try:
                    apply_file_changes(changes, before_write=before_manifest_write)
                except FileTransactionError as exc:
                    effects.extend(CommittedEffect("workspace.bound", str(path)) for path in exc.surviving_paths)
                    raise
                effects.extend(CommittedEffect("workspace.bound", str(change.path)) for change in changes)
                write_status = "changed" if changes else "noop"
            before_scaffold = tuple(_observe_file(Path(item.identity)) for item in local
                                    if Path(item.identity).name in {".gitignore", "exclude"})
            try:
                ensure_brain_ignore_rules(target, "project", [], skip_mcp=True, before_write=revalidate_scaffold)
            finally:
                for item in before_scaffold:
                    if _observe_file(Path(item.identity)) != item:
                        effects.append(CommittedEffect("workspace.scaffolded", "caller-workspace:" + item.identity))
    except (OSError, ValueError, WorkspaceBindingError, MutationLockError, GitInspectionError, TransitionIndexesIncomplete, FileTransactionError) as exc:
        return registration_error(request, exc, effects)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, WorkspaceRegisterPayload(
        registration, WorkspaceBindingOutcome(manifest["brain"], manifest["slug"], key, write_status), False),
        committed_effects=tuple(effects))


def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {"brain_id", "slug"})
    return WorkspaceRegisterRequest(
        require_string(payload.get("brain_id"), "brain_id", optional=True),
        require_string(payload.get("slug"), "slug", optional=True),
    )


def catalogue_entry():
    return compound_workspace_entry(WorkspaceRegisterRequest, execute)
