"""Requested byte-preserving repair of the closed clear-text diagnosis set."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Mapping, TYPE_CHECKING

from _portable_path import validate_portable_relative_path

if TYPE_CHECKING:
    from _common._text_encoding import TextDiagnosis
from _lifecycle.text_repair_io import read_candidate, repair_candidate, TextRepairIOError
from .._decoding import reject_unexpected
from .._mutation_support import maintainer_mutation_entry, no_effect_error
from ..preparation import OperationPreparation, ObservedResource, admit_owner, bind_operation, content_digest
from ..receipts import CommittedEffect
from ..results import CommandError, CommandNextAction, CommandWarning, ErrorCode, Ok, Partial, RequestErrorDetails, WarningCode


@dataclass(frozen=True, slots=True)
class TextRepairFile:
    path: str
    code: str
    status: str
    before_bytes: int
    after_bytes: int
    dropped_hex: str
    dropped_windows1252: str


@dataclass(frozen=True, slots=True)
class TextRepairPayload:
    dry_run: bool
    files: tuple[TextRepairFile, ...]


@dataclass(frozen=True, slots=True)
class RepairTextRequest:
    COMMAND_ID: ClassVar[str] = "vault.repair-text"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TextRepairPayload
    paths: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.paths is not None:
            if not isinstance(self.paths, tuple) or any(not isinstance(path, str) for path in self.paths):
                raise ValueError("paths must be an array of vault-relative paths")
            for path in self.paths:
                validate_portable_relative_path(path)
            if len(set(self.paths)) != len(self.paths):
                raise ValueError("paths must not contain duplicates")


@dataclass(frozen=True, slots=True)
class PlannedTextRepair:
    path: str
    diagnosis: TextDiagnosis
    digest: str
    before_bytes: int

    def preview(self, status):
        diagnosis = self.diagnosis
        return TextRepairFile(self.path, diagnosis.code, status, self.before_bytes,
            len(diagnosis.fixed_bytes), diagnosis.dropped_bytes.hex(" "),
            diagnosis.dropped_bytes.decode("cp1252", errors="backslashreplace"))


def plan_text_repair(root, request):
    from _common._text_encoding import diagnose_text
    from _lifecycle.text_files import iter_vault_text_files
    """Diagnose current filesystem candidates without requiring a router."""
    root = Path(root)
    inventory = set(iter_vault_text_files(root))
    selected = inventory if request.paths is None else set(request.paths)
    outside = selected - inventory
    if outside:
        raise ValueError("No current text finding for: " + ", ".join(sorted(outside)))
    plan = []
    for relative in sorted(selected):
        try:
            raw = read_candidate(root, relative)
        except FileNotFoundError:
            if request.paths is not None:
                raise ValueError(f"No current text finding for: {relative}") from None
            continue
        diagnosis = diagnose_text(raw)
        if diagnosis is None or diagnosis.fixed_bytes is None:
            if request.paths is not None:
                raise ValueError(f"No current text_encoding finding for: {relative}")
            continue
        plan.append(PlannedTextRepair(relative, diagnosis, content_digest(raw), len(raw)))
    return tuple(plan)


def repair_binding(context, request, *, plan, frozen_inputs=None):
    return bind_operation(request,
        observations=tuple(ObservedResource("text-file", item.path, item.digest) for item in plan),
        frozen_inputs=frozen_inputs,
        review={"operation": "Repair the selected clear text diagnoses.",
                "files": [dict(path=item.path, code=item.diagnosis.code,
                    before_bytes=item.before_bytes, after_bytes=len(item.diagnosis.fixed_bytes),
                    dropped_hex=item.diagnosis.dropped_bytes.hex(" "),
                    dropped_windows1252=item.diagnosis.dropped_bytes.decode("cp1252", errors="backslashreplace")) for item in plan]})


def prepare(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    root = context.selected_brain.vault_root
    with vault_mutation_lock(root):
        return repair_binding(context, request, plan=plan_text_repair(root, request), frozen_inputs=frozen_inputs)


def execute(context, request):
    from _common import MutationLockError, vault_mutation_lock
    from _lifecycle.text_files import iter_vault_text_files
    root = Path(context.selected_brain.vault_root)
    effects, files, warnings, failures = [], [], [], []
    pending_error = None
    try:
        with vault_mutation_lock(root):
            plan = plan_text_repair(root, request)
            admit_owner(context, request, repair_binding, plan=plan)
            for item in plan:
                if not item.diagnosis.dropped_bytes:
                    warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                        f"{item.path}: {item.diagnosis.code}; planned repair converts this file to UTF-8 without a byte-order mark."))
                if item.diagnosis.dropped_bytes:
                    warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                        f"{item.path}: truncated_utf8 drops {item.diagnosis.dropped_bytes.hex(' ')} "
                        f"(Windows-1252: {item.diagnosis.dropped_bytes.decode('cp1252', errors='backslashreplace')}). "
                        "Content after the cut may already be lost. Use paths to leave this file out, or convert it manually in an editor."))
            if context.dry_run:
                return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
                          TextRepairPayload(True, tuple(item.preview("planned") for item in plan)), warnings=tuple(warnings))
            inventory = set(iter_vault_text_files(root))
            for item in plan:
                try:
                    if item.path not in inventory or not repair_candidate(root, item.path, item.digest.removeprefix("sha256:"), item.diagnosis.fixed_bytes):
                        files.append(item.preview("skipped: changed"))
                        warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED,
                            f"{item.path}: skipped: changed after text repair planning."))
                        continue
                except (OSError, ValueError) as exc:
                    failures.append(f"{item.path}: {exc}")
                    if isinstance(exc, TextRepairIOError) and exc.committed:
                        effects.append(CommittedEffect(request.COMMAND_ID, item.path))
                        files.append(item.preview("changed"))
                    else:
                        files.append(item.preview("failed"))
                    continue
                effects.append(CommittedEffect(request.COMMAND_ID, item.path))
                files.append(item.preview("changed"))
            maintenance_error = None
            if effects:
                from .._transition_indexes import reconcile_transition_indexes, TransitionIndexesIncomplete
                try:
                    reconcile_transition_indexes(context, changed_paths=tuple(effect.subject for effect in effects))
                except TransitionIndexesIncomplete as exc:
                    maintenance_error = exc.error
                    failures.append(exc.error.message)
            if failures:
                message = "; ".join(failures)
                error = (replace(maintenance_error, message=message) if maintenance_error else
                         CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails("paths", message), CommandNextAction("vault.check")))
                pending_error = error
                if effects:
                    return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error, tuple(effects), tuple(warnings))
                return no_effect_error(RepairTextRequest, ErrorCode.CONFLICT, message)
            return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
                      TextRepairPayload(False, tuple(files)), tuple(effects), tuple(warnings))
    except (MutationLockError, OSError, ValueError) as exc:
        if effects:
            error = (replace(pending_error, message=pending_error.message + "; " + str(exc)) if pending_error else
                     CommandError(ErrorCode.CONFLICT, str(exc), RequestErrorDetails("paths", str(exc)), CommandNextAction("vault.check")))
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error, tuple(effects), tuple(warnings))
        return no_effect_error(RepairTextRequest, ErrorCode.CONFLICT, str(exc))


def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {"paths"})
    paths = payload.get("paths")
    if paths is not None and (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)):
        raise ValueError("paths must be an array of vault-relative paths")
    return RepairTextRequest(None if paths is None else tuple(paths))


def catalogue_entry():
    return replace(maintainer_mutation_entry(RepairTextRequest, execute),
                   summary="Repair clear text encoding findings with byte-preserving writes.",
                   preparation=OperationPreparation(prepare))
