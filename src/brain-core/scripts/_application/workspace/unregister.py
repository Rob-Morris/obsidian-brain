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
    workspace_admission,
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
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = WorkspaceUnregisterPayload

    key: str

    def __post_init__(self) -> None:
        validate_slug(self.key)


def linked_folder(vault_root: Path, key: str) -> Path | None:
    """The folder the Brain's registry row records for ``key``, or ``None`` without a row."""
    import workspace_registry

    row = workspace_registry.load_registry_strict(vault_root).get(key)
    return None if row is None else Path(workspace_registry.canonical_path(row["path"]))


def _refusal(vault_root: Path, folder: Path, key: str) -> str | None:
    """Why the folder is not this link's other end, in words, or ``None`` when it is."""
    from _bootstrap.workspace_binding import LinkVerdict, classify_link

    verdict = classify_link(vault_root, folder, key)
    return {
        LinkVerdict.MATCHES: None,
        LinkVerdict.UNREACHABLE: "the folder is unreachable",
        LinkVerdict.VAULT_ROOT: "the folder is a Brain vault root",
        LinkVerdict.NO_MANIFEST: "the folder has no workspace manifest",
        LinkVerdict.UNREADABLE: f"the folder could not be inspected ({verdict.detail})",
        LinkVerdict.BRAIN_UNRESOLVED: "its manifest's Brain ID does not resolve on this machine",
        LinkVerdict.KEY_MISSING: "its manifest names no workspace key",
        LinkVerdict.OTHER_BRAIN: "its manifest names another Brain",
        LinkVerdict.OTHER_KEY: "its manifest names another workspace",
    }[verdict.verdict]


def _manifest_snapshot(folder: Path) -> tuple[bytes | None, ...]:
    from _bootstrap.workspace_binding import legacy_manifest_path_for, manifest_path_for

    snapshot = []
    for path in (manifest_path_for(folder), legacy_manifest_path_for(folder)):
        try:
            snapshot.append(path.read_bytes())
        except FileNotFoundError:
            snapshot.append(None)
        except OSError:
            snapshot.append(b"unreadable")
    return tuple(snapshot)


def _unlink(vault_root: Path, folder: Path, key: str) -> str | None:
    """Unlink the manifest under the folder's own lock; return a refusal if it is no longer this link."""
    from _common import vault_mutation_lock
    from _bootstrap.workspace_binding import load_workspace_manifest_state, save_workspace_manifest_data, unlinked_payload
    import workspace_registry

    with ExitStack() as stack:
        try:
            # Never recreate a folder that has gone: only .brain/local may be made, under an existing .brain.
            (folder / ".brain" / "local").mkdir(exist_ok=True)
            stack.enter_context(vault_mutation_lock(folder, create_parent=False))
        except FileNotFoundError:
            return "the folder is unreachable"
        refusal = _refusal(vault_root, folder, key)
        if refusal is None and key in workspace_registry.load_registry(vault_root):
            refusal = f"a registry row for {key} was written again while unregistering"
        if refusal is None:
            state = load_workspace_manifest_state(folder)
            save_workspace_manifest_data(folder, unlinked_payload(state.data), state=state)
        return refusal


def execute(context: InvocationContext, request: WorkspaceUnregisterRequest):
    from _common import MutationLockError, public_mutation_error_message, vault_mutation_lock
    from _bootstrap.workspace_binding import WorkspaceBindingError, manifest_path_for
    import workspace_registry

    root = context.selected_brain.vault_root
    key = request.key
    try:
        with vault_mutation_lock(root):
            folder = linked_folder(root, key)
            if folder is None:
                return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, workspace_registry.missing_row_message(key))
            before_write = workspace_admission(context, request)
            if context.dry_run:
                before_write()
            else:
                workspace_registry.unregister_workspace(root, key, before_write=before_write)
    except MutationLockError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, public_mutation_error_message(exc), retryable=True)
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    planned = context.dry_run
    steps = [CallerWorkspaceStep(
        "workspace_registry", "planned" if planned else "changed",
        f"{'Would remove' if planned else 'Removed'} linked workspace {key} from the registry.")]
    effects = [] if planned else [CommittedEffect(request.COMMAND_ID, f"caller-workspace-registration:{key}")]
    manifest = manifest_path_for(folder)
    # The two locks are never held together: the folder is locked only after the row is gone.
    refusal = _refusal(root, folder, key)
    if refusal is None and not planned:
        before = _manifest_snapshot(folder)
        try:
            refusal = _unlink(root, folder, key)
        except (MutationLockError, OSError, ValueError, WorkspaceBindingError) as exc:
            if _manifest_snapshot(folder) != before:
                effects.append(CommittedEffect("workspace.unbound", str(manifest)))
            message = (f"Removed linked workspace {key} from the registry, but {manifest} still links it ({exc}): "
                       "remove its brain and links.workspace fields, or run `brain workspace setup` from that "
                       "folder to link it again.")
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
                           tuple(effects))
        if refusal is None:
            effects.append(CommittedEffect("workspace.unbound", str(manifest)))
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
            f"The workspace manifest in {folder} was not changed because {refusal}."),)
    status = CallerWorkspaceStatus.PLANNED if planned else CallerWorkspaceStatus.CHANGED
    payload = WorkspaceUnregisterPayload(key, str(folder), status, planned, tuple(steps))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload,
              committed_effects=tuple(effects), warnings=warnings)


def decode(payload: Mapping[str, object]) -> WorkspaceUnregisterRequest:
    reject_unexpected(payload, {"key"})
    return WorkspaceUnregisterRequest(require_string(payload.get("key"), "key"))


def catalogue_entry():
    return compound_workspace_entry(WorkspaceUnregisterRequest, execute)
