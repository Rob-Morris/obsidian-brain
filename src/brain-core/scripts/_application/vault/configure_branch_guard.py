"""Explicit selected-vault policy configuration, including blocked-branch recovery."""

from dataclasses import dataclass, field, replace
from typing import ClassVar

from .._decoding import reject_unexpected
from .._mutation_support import maintainer_mutation_entry
from ..preparation import OperationPreparation, admit_owner, bind_operation
from ..receipts import CommittedEffect
from ..results import Ok
from ..types import BranchGuardState


@dataclass(frozen=True, slots=True)
class BranchGuardConfigurationPayload:
    changed: bool
    branch_guard: BranchGuardState


@dataclass(frozen=True, slots=True)
class VaultConfigureBranchGuardRequest:
    COMMAND_ID: ClassVar[str] = "vault.configure-branch-guard"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BranchGuardConfigurationPayload
    MINIMAL_EXAMPLE: ClassVar[dict] = {
        "allowed_branches": ["dev/feature"], "expected_revision": "missing",
    }
    allowed_branches: tuple[str, ...] | None = field(metadata={"description": "Nonempty unique literal Git branch names; null explicitly removes the local policy."})
    expected_revision: str = field(metadata={"description": "Complete local-file revision from vault.read-config, including its missing-file revision."})

    def __post_init__(self):
        if self.allowed_branches is not None:
            from _bootstrap.branch_guard import BranchGuardError, validate_allowed_branches
            if not isinstance(self.allowed_branches, tuple):
                raise ValueError("allowed_branches must be a tuple or null")
            try:
                validate_allowed_branches(list(self.allowed_branches))
            except BranchGuardError as exc:
                raise ValueError("allowed_branches must contain unique literal Git branch names") from exc
        if not isinstance(self.expected_revision, str) or not self.expected_revision:
            raise ValueError("expected_revision is required")


def prepare(context, request, *, frozen_inputs=None):
    return bind_operation(request, frozen_inputs=frozen_inputs,
        review={"operation": "Explicitly replace or remove only the selected vault's local branch guard."})


def execute(context, request):
    from _bootstrap.branch_guard import BranchGuardError, configure_guard, inspect_guard, observe_git
    from _bootstrap.local_config import LocalConfigError, read_local_config
    from _bootstrap.file_lock import vault_mutation_lock
    from ..application import branch_guard_error_result
    from ..results import ErrorCode, request_error

    root = context.selected_brain.vault_root
    admit_owner(context, request, prepare)
    try:
        if context.dry_run:
            with vault_mutation_lock(root):
                current = read_local_config(root)
                if current.revision != request.expected_revision:
                    raise LocalConfigError("Local configuration changed; read vault.read-config and retry with its current revision.")
                if request.allowed_branches is not None and observe_git(root).repository is None:
                    raise BranchGuardError("outside-git")
                return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
                    BranchGuardConfigurationPayload(False, BranchGuardState.from_inspection(inspect_guard(root))))
        result = configure_guard(root,
            None if request.allowed_branches is None else list(request.allowed_branches),
            request.expected_revision)
    except BranchGuardError as exc:
        return branch_guard_error_result(request.COMMAND_ID, request.COMMAND_VERSION, exc)
    except (LocalConfigError, ValueError) as exc:
        return request_error(type(request), ErrorCode.CONFLICT, str(exc), "expected_revision")
    state = BranchGuardState.from_inspection(inspect_guard(root))
    effects = (CommittedEffect(request.COMMAND_ID, ".brain/local/config.yaml"),) if result["changed"] else ()
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
              BranchGuardConfigurationPayload(result["changed"], state), effects)


def decode(payload):
    reject_unexpected(payload, {"allowed_branches", "expected_revision"})
    if "allowed_branches" not in payload:
        raise ValueError("allowed_branches is required; use null for explicit removal")
    value = payload["allowed_branches"]
    if value is not None and not isinstance(value, list):
        raise ValueError("allowed_branches must be a list or null")
    return VaultConfigureBranchGuardRequest(None if value is None else tuple(value), payload.get("expected_revision"))


def catalogue_entry():
    return replace(maintainer_mutation_entry(VaultConfigureBranchGuardRequest, execute),
        preparation=OperationPreparation(prepare),
        summary="Explicitly replace or remove the selected vault's local Git branch guard while preserving other settings.")
