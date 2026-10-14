"""Typed ``workspace.unregister`` owner: remove one workspace link from both ends."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Mapping

from .._caller_workspace import (
    CallerWorkspaceStatus,
    CallerWorkspaceStep,
    compound_workspace_entry,
    reject_unexpected,
    require_string,
)
from .._mutation_support import no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import CommandError, CommandWarning, ErrorCode, Ok, Partial, RequestErrorDetails, WarningCode
from ..types import validate_slug


@dataclass(frozen=True, slots=True)
class WorkspaceUnregisterPayload:
    key: str
    folder: str
    status: CallerWorkspaceStatus
    dry_run: bool
    steps: tuple[CallerWorkspaceStep, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceUnregisterRequest:
    COMMAND_ID: ClassVar[str] = "workspace.unregister"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = WorkspaceUnregisterPayload

    key: str | None = None

    def __post_init__(self) -> None:
        if self.key is not None:
            validate_slug(self.key)


def selected_row(context, request):
    import workspace_registry

    folder = context.workspace_dir
    if folder is None:
        raise ValueError("Select the recorded folder with --workspace PATH; a slug cannot select a folder")
    rows, expected = workspace_registry.read_registry_strict(context.selected_brain.vault_root)
    path = workspace_registry.canonical_path(folder)
    row = rows.get(path)
    if row is None:
        raise ValueError(f"Folder {path} has no registration in the selected Brain")
    if request.key is not None and request.key != row["slug"]:
        raise ValueError("The supplied key does not match the selected folder registration")
    return Path(path), row["slug"], rows, expected


def prepare(context, request, *, frozen_inputs=None):
    from ..preparation import bind_operation, ObservedResource, content_digest
    from ._preparation import _observe_file, _observed_or_absent
    from _bootstrap.workspace_binding import manifest_path_for, legacy_manifest_path_for
    from _bootstrap.mcp_registration import require_no_registered_integrations
    import workspace_registry

    root = context.selected_brain.vault_root
    folder, key, _, _ = selected_row(context, request)
    require_no_registered_integrations(root, folder)
    observations = [_observe_file(Path(workspace_registry._registry_path(root)))]
    for path in (manifest_path_for(folder), legacy_manifest_path_for(folder)):
        observations.append(ObservedResource("linked-manifest", str(path),
            _observed_or_absent(lambda path=path: content_digest(path.read_bytes()))))
    def identity():
        stat = folder.stat()
        return f"{stat.st_dev}:{stat.st_ino}" if folder.is_dir() else None
    observations.append(ObservedResource("linked-folder", str(folder), _observed_or_absent(identity)))
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"folder": str(folder), "workspace": "workspace/" + key,
                                  "binding": _refusal(root, folder, key) or "matching"})

def _refusal(vault_root: Path, folder: Path, key: str) -> str | None:
    """Why the folder is not this link's other end, in words, or ``None`` when it is."""
    from _bootstrap.workspace_binding import classify_link, describe_link

    return describe_link(classify_link(vault_root, folder, key))


def _unlink(vault_root: Path, folder: Path, key: str) -> str | None:
    """Unlink the manifest under the folder's own lock; return a refusal if it is no longer this link."""
    from _common import vault_mutation_lock
    from _bootstrap.workspace_binding import load_workspace_manifest_state, unlinked_payload
    from _bootstrap.file_transaction import FilePlan, apply_file_changes
    from .register import stage_manifest
    import workspace_registry

    from _bootstrap.mcp_registration import registration_lock, require_no_registered_integrations
    with ExitStack() as stack:
        stack.enter_context(registration_lock(Path.home()))
        stack.enter_context(vault_mutation_lock(vault_root))
        require_no_registered_integrations(vault_root, folder)
        try:
            # The existing .brain parent is required; never recreate a vanished folder.
            (folder / ".brain" / "local").mkdir(exist_ok=True)
            stack.enter_context(vault_mutation_lock(folder, create_parent=False))
        except FileNotFoundError:
            return "the folder is unreachable"
        refusal = _refusal(vault_root, folder, key)
        if refusal is None and workspace_registry.canonical_path(folder) in workspace_registry.load_registry_strict(vault_root):
            refusal = f"a registry row for {key} was written again while unregistering"
        if refusal is None:
            state = load_workspace_manifest_state(folder)
            plan = FilePlan()
            stage_manifest(plan, state, unlinked_payload(state.data))
            plan.validate()
            apply_file_changes(plan.changes(), before_write=plan.validate_dependencies)
        return refusal


def execute(context: InvocationContext, request: WorkspaceUnregisterRequest):
    from _common import MutationLockError, public_mutation_error_message, vault_mutation_lock
    from _bootstrap.workspace_binding import WorkspaceBindingError, manifest_path_for, manifest_snapshot
    import workspace_registry

    from _bootstrap.mcp_registration import registration_lock, require_no_registered_integrations
    from _bootstrap.file_transaction import FileTransactionError
    root = context.selected_brain.vault_root
    try:
        with registration_lock(Path.home()), vault_mutation_lock(root):
            folder, key, rows, expected = selected_row(context, request)
            require_no_registered_integrations(root, folder)
            context.admission.admit(prepare(context, request, frozen_inputs=context.admission.frozen_inputs))
            if not context.dry_run:
                del rows[workspace_registry.canonical_path(folder)]
                workspace_registry.replace_registry(root, rows, expected=expected)
    except MutationLockError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, public_mutation_error_message(exc), retryable=True)
    except workspace_registry.RegistryChangedError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc), retryable=True)
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    planned = context.dry_run
    steps = [CallerWorkspaceStep(
        "workspace_registry", "planned" if planned else "changed",
        f"{'Would remove' if planned else 'Removed'} linked workspace {key} from the registry.")]
    effects = [] if planned else [CommittedEffect(request.COMMAND_ID, f"caller-workspace-registration:{folder}")]
    manifest = manifest_path_for(folder)
    # Lock order is vault, then folder, never the reverse; here the folder is locked only after
    # the vault lock is released, so the two are never held together.
    refusal = _refusal(root, folder, key)
    if refusal is None and not planned:
        before = manifest_snapshot(folder)
        try:
            refusal = _unlink(root, folder, key)
        except (MutationLockError, OSError, ValueError, WorkspaceBindingError, FileTransactionError) as exc:
            if isinstance(exc, FileTransactionError):
                effects.extend(CommittedEffect("workspace.unbound", str(path)) for path in exc.surviving_paths)
            elif not manifest_snapshot(folder).confirms(before):
                effects.append(CommittedEffect("workspace.unbound", str(manifest)))
            message = (f"Removed linked workspace {key} from the registry, but {manifest} still links it ({exc}): "
                       "remove its brain and slug fields, or run `brain workspace register` from that "
                       "folder to link it again.")
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
                           tuple(effects))
        if refusal is None:
            effects.append(CommittedEffect("workspace.unbound", str(manifest)))
    import json
    import shlex
    recovery = (f"brain workspace register --vault {shlex.quote(str(root))} "
                f"--workspace {shlex.quote(str(folder))} "
                f"--request-json {shlex.quote(json.dumps({"slug": key}))}")
    warnings = ()
    if refusal is None:
        steps.append(CallerWorkspaceStep(
            "workspace_binding", "planned" if planned else "changed",
            f"{'Would unlink' if planned else 'Unlinked'} the workspace manifest at {manifest}."))
    else:
        steps.append(CallerWorkspaceStep(
            "workspace_binding", "noop", f"Left the folder {folder} untouched: {refusal}."))
        warnings = (CommandWarning(
            WarningCode.FOLLOW_UP_REQUIRED,
            f"The workspace manifest in {folder} was not changed because {refusal}. "
            f"Check or restore that folder and its metadata, then reconcile explicitly with: {recovery}. "
            "If metadata is outdated, run workspace upgrade first; a different binding requires explicit rebind."),)
    status = CallerWorkspaceStatus.PLANNED if planned else CallerWorkspaceStatus.CHANGED
    payload = WorkspaceUnregisterPayload(key, str(folder), status, planned, tuple(steps))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload,
              committed_effects=tuple(effects), warnings=warnings)


def decode(payload: Mapping[str, object]) -> WorkspaceUnregisterRequest:
    reject_unexpected(payload, {"key"})
    return WorkspaceUnregisterRequest(require_string(payload.get("key"), "key", optional=True))


def catalogue_entry():
    return compound_workspace_entry(WorkspaceUnregisterRequest, execute)
