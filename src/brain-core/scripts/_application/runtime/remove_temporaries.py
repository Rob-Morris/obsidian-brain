"""Typed ``runtime.remove-temporaries`` owner (DD-082, D18)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._decoding import decode_empty
from .._mutation_support import derived_cache_maintenance_entry, no_effect_error
from ..context import InvocationContext
from ..preparation import ObservedResource, OperationPreparation, admit_owner, bind_operation
from ..receipts import CommittedEffect
from ..results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails


class TemporariesRemovalStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class RuntimeRemoveTemporariesPayload:
    status: TemporariesRemovalStatus
    dry_run: bool
    removed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeRemoveTemporariesRequest:
    COMMAND_ID: ClassVar[str] = "runtime.remove-temporaries"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeRemoveTemporariesPayload


def _candidates(context: InvocationContext) -> tuple[str, ...]:
    from _bootstrap.stranded_temporaries import find_stranded_temporaries

    return find_stranded_temporaries(context.selected_brain.vault_root, context.clock.now())


def temporaries_binding(context, request, *, frozen_inputs=None):
    root = context.selected_brain.vault_root
    observations = []
    for rel_path in _candidates(context):
        info = (root / rel_path).lstat()
        observations.append(ObservedResource("temporary-file", rel_path, f"{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}"))
    return bind_operation(
        request,
        observations=observations,
        frozen_inputs=frozen_inputs,
        review={"operation": "Remove stranded atomic-write temporaries from .brain/local",
                "files": [item.identity for item in observations]},
    )


def prepare_temporaries(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock

    with vault_mutation_lock(context.selected_brain.vault_root):
        return temporaries_binding(context, request, frozen_inputs=frozen_inputs)


def execute(context: InvocationContext, request: RuntimeRemoveTemporariesRequest):
    from _common import MutationLockError, public_mutation_error_message, vault_mutation_lock

    root = context.selected_brain.vault_root
    removed: list[str] = []
    try:
        with vault_mutation_lock(root):
            # Re-list under the lock: the repair acts on the vault as it is,
            # not on the finding that triggered it.
            planned = _candidates(context)
            admit_owner(context, request, temporaries_binding)
            if context.dry_run:
                status = TemporariesRemovalStatus.PLANNED if planned else TemporariesRemovalStatus.NOOP
                return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
                          RuntimeRemoveTemporariesPayload(status, True, planned))
            for rel_path in planned:
                target = root / rel_path
                try:
                    target.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    return _failure(request, f"could not remove {rel_path}: {exc}", removed)
                removed.append(rel_path)
    except MutationLockError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, public_mutation_error_message(exc), retryable=True)

    status = TemporariesRemovalStatus.CHANGED if removed else TemporariesRemovalStatus.NOOP
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RuntimeRemoveTemporariesPayload(status, False, tuple(removed)),
        committed_effects=tuple(CommittedEffect(request.COMMAND_ID, rel_path) for rel_path in removed),
    )


def _failure(request, message: str, removed: list[str]):
    if removed:
        return Partial(
            request.COMMAND_ID, request.COMMAND_VERSION,
            CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
            tuple(CommittedEffect(request.COMMAND_ID, rel_path) for rel_path in removed),
        )
    return no_effect_error(type(request), ErrorCode.CONFLICT, message)


def decode(payload: Mapping[str, object]) -> RuntimeRemoveTemporariesRequest:
    return decode_empty(payload, RuntimeRemoveTemporariesRequest)


def catalogue_entry():
    return derived_cache_maintenance_entry(
        RuntimeRemoveTemporariesRequest, execute, OperationPreparation(prepare_temporaries),
        summary="Remove stranded atomic-write temporary files from .brain/local.")

