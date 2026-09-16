"""One admitted Brain-first setup operation across two separately locked boundaries."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Mapping

from .._caller_workspace import (caller_workspace_entry, decode_workspace_binding,
    validate_workspace_binding_request, workspace_dir)
from ..preparation import bind_operation, ObservedResource
from ..receipts import CommittedEffect
from ..results import Error, Ok
from ..types import DependencyTier, EffectClass, Locality
from ._registration import WorkspaceRegistration, plan_registration, apply_registration
from .ensure_registration import registration_error


@dataclass(frozen=True, slots=True)
class WorkspaceBindingOutcome:
    brain: str
    slug: str
    workspace: str
    status: str


@dataclass(frozen=True, slots=True)
class WorkspaceSetupPayload:
    registration: WorkspaceRegistration
    binding: WorkspaceBindingOutcome
    dry_run: bool


@dataclass(frozen=True, slots=True)
class WorkspaceSetupRequest:
    COMMAND_ID: ClassVar[str] = "workspace.setup"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = WorkspaceSetupPayload

    brain_id: str | None = None
    slug: str | None = None
    force: bool = False

    def __post_init__(self):
        validate_workspace_binding_request(self)


def plan_setup(context, request, *, frozen_inputs=None):
    import vault_registry
    import workspace_registry
    from _bootstrap.workspace_binding import plan_workspace_binding, resolve_local_brain_vault
    from _bootstrap.workspace_scaffold import _git_dir, _git_repo_root
    from _common._workspace import manifest_workspace_reference, workspace_policy
    from ._preparation import _observe_file

    root, target = context.selected_brain.vault_root, context.workspace_dir
    if target is None or not target.is_dir():
        raise ValueError("workspace setup requires an existing caller directory")
    alias = request.brain_id
    if alias is None:
        aliases = sorted(key for key, entry in vault_registry.load_registry_entries().items()
                         if entry.kind == "local" and Path(entry.value).resolve() == root.resolve())
        if not aliases:
            raise ValueError("Selected Brain has no machine registration; register the Brain before workspace setup")
        alias = aliases[0]
    selected = resolve_local_brain_vault(alias)
    if selected != root.resolve():
        raise ValueError("Setup brain_id must identify the selected Brain; select that Brain before setup")
    state, manifest = plan_workspace_binding(target, brain=alias, slug=request.slug,
                                             allow_rebind=request.force)
    links = manifest.get("links", {})
    if not isinstance(links, dict):
        raise ValueError("Workspace links must be a mapping")
    reference = (manifest_workspace_reference(manifest) if "workspace" in links
                 else "workspace/" + manifest["slug"])
    key = reference.split("/", 1)[1]
    router, registration, plan, observations, frozen = plan_registration(
        context, key, frozen_inputs=frozen_inputs)
    workspace_policy(router, reference, manifest.get("defaults", {}), local=True)
    manifest["links"] = {**links, "workspace": key}
    registry = workspace_registry.load_registry(root)
    embedded = root / workspace_registry.EMBEDDED_DATA_DIR / key
    if embedded.exists():
        raise ValueError(f"Cannot register linked workspace {key}: embedded workspace exists")
    observations.append(ObservedResource("embedded-workspace", str(embedded), None))
    files = {state.manifest_path, state.legacy_path, Path(vault_registry.registry_path()),
             Path(workspace_registry._registry_path(root))}
    if _git_repo_root(target) == target.resolve():
        files.add(target / ".gitignore")
        gitdir = _git_dir(target)
        if gitdir is not None and not (target / ".gitignore").exists():
            files.add(gitdir / "info/exclude")
    stat = target.stat()
    observations.append(ObservedResource("caller-workspace", str(target.resolve()), f"{stat.st_dev}:{stat.st_ino}"))
    observations.extend(_observe_file(path) for path in sorted(files))
    binding = bind_operation(request, observations=observations, frozen_inputs=frozen,
        review={"registration": reference, "path": registration.path,
                "binding": manifest, "workspace": str(target), "order": "Brain registration, then caller binding"})
    local_observations = tuple(_observe_file(path) for path in sorted(files)
                              if path == state.manifest_path or path == state.legacy_path or path.name in {".gitignore", "exclude"})
    return router, registration, plan, state, manifest, registry, binding, local_observations


def prepare_setup(context, request, *, frozen_inputs=None):
    return plan_setup(context, request, frozen_inputs=frozen_inputs)[6]


def execute(context, request):
    from _common import vault_mutation_lock, MutationLockError
    from _bootstrap.workspace_binding import save_workspace_manifest_data, WorkspaceBindingError
    from _bootstrap.workspace_scaffold import ensure_brain_ignore_rules, GitInspectionError
    from .._transition_indexes import TransitionIndexesIncomplete
    from ._preparation import _observe_file
    import workspace_registry

    target = workspace_dir(context, type(request))
    if isinstance(target, Error):
        return target
    effects = []
    try:
        with vault_mutation_lock(context.selected_brain.vault_root):
            router, registration, plan, state, manifest, registry, binding, local = plan_setup(
                context, request, frozen_inputs=context.admission.frozen_inputs)
            context.admission.admit(binding)
            key = manifest["links"]["workspace"]
            if not context.dry_run:
                apply_registration(context, router, registration, plan, effects)
                if registry.get(key) != {"path": str(target)}:
                    workspace_registry.register_workspace(context.selected_brain.vault_root, key, target)
                    effects.append(CommittedEffect("workspace.path-registered", f"selected-brain:.brain/local/workspaces.json#{key}"))
        if context.dry_run:
            return Ok(request.COMMAND_ID, request.COMMAND_VERSION, WorkspaceSetupPayload(
                replace(registration, status="planned"), WorkspaceBindingOutcome(manifest["brain"], manifest["slug"], key, "planned"), True))
        with vault_mutation_lock(target):
            # A separate caller lock must recheck the admitted local observations.
            identity = next(item for item in binding.observations if item.kind == "caller-workspace")
            stat = target.stat()
            if identity.identity != str(target.resolve()) or identity.revision != f"{stat.st_dev}:{stat.st_ino}":
                raise ValueError("Caller workspace identity changed after admission; retry setup")
            if any(_observe_file(Path(item.identity)) != item for item in local):
                raise ValueError("Caller workspace changed after admission; Brain registration is complete, retry setup")
            before_manifest = _observe_file(state.manifest_path)
            try:
                write = save_workspace_manifest_data(target, manifest, state=state)
            finally:
                # Legacy cleanup may fail after the canonical manifest committed.
                if _observe_file(state.manifest_path) != before_manifest:
                    effects.append(CommittedEffect("workspace.bound", "caller-workspace:.brain/local/workspace.yaml"))
            before_scaffold = tuple(_observe_file(Path(item.identity)) for item in local
                                    if Path(item.identity).name in {".gitignore", "exclude"})
            try:
                ensure_brain_ignore_rules(target, "project", [], skip_mcp=True)
            finally:
                for item in before_scaffold:
                    if _observe_file(Path(item.identity)) != item:
                        effects.append(CommittedEffect("workspace.scaffolded", "caller-workspace:" + item.identity))
    except (OSError, ValueError, WorkspaceBindingError, MutationLockError, GitInspectionError, TransitionIndexesIncomplete) as exc:
        return registration_error(request, exc, effects)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, WorkspaceSetupPayload(
        registration, WorkspaceBindingOutcome(manifest["brain"], manifest["slug"], key, write.status), False),
        committed_effects=tuple(effects))


def decode(payload: Mapping[str, object]):
    return decode_workspace_binding(payload, WorkspaceSetupRequest)


def catalogue_entry():
    return replace(caller_workspace_entry(WorkspaceSetupRequest, execute),
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_AND_CALLER_LOCAL,
        effect_class=EffectClass.SELECTED_BRAIN_AND_CALLER_LOCAL_MUTATION)
