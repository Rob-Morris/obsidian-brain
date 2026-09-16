"""Selected-Brain workspace identity registration."""

from dataclasses import dataclass, replace
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._caller_workspace import require_string
from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..preparation import OperationPreparation, bind_operation
from ..results import ErrorCode, Ok, Partial, CommandError, RequestErrorDetails
from ..types import validate_slug
from ._registration import WorkspaceRegistration, plan_registration, apply_registration


@dataclass(frozen=True, slots=True)
class WorkspaceEnsureRegistrationRequest:
    COMMAND_ID: ClassVar[str] = "workspace.ensure-registration"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceRegistration
    MINIMAL_EXAMPLE: ClassVar[dict] = {"key": "example"}

    key: str
    title: str | None = None

    def __post_init__(self):
        validate_slug(self.key)
        require_string(self.title, "title", optional=True)


def prepare(context, request, *, frozen_inputs=None):
    _, registration, _, observations, frozen = plan_registration(
        context, request.key, request.title, frozen_inputs=frozen_inputs)
    return bind_operation(request, observations=observations, frozen_inputs=frozen,
                          review={"registration": registration.reference, "path": registration.path,
                                  "status": registration.status})


def execute(context, request):
    from _common import vault_mutation_lock, MutationLockError
    from .._transition_indexes import TransitionIndexesIncomplete

    effects = []
    try:
        with vault_mutation_lock(context.selected_brain.vault_root):
            router, registration, plan, observations, frozen = plan_registration(
                context, request.key, request.title, frozen_inputs=context.admission.frozen_inputs)
            context.admission.admit(bind_operation(request, observations=observations, frozen_inputs=frozen,
                review={"registration": registration.reference, "path": registration.path,
                        "status": registration.status}))
            if not context.dry_run:
                apply_registration(context, router, registration, plan, effects)
            else:
                registration = replace(registration, status="planned")
    except (OSError, ValueError, MutationLockError, TransitionIndexesIncomplete) as exc:
        return registration_error(request, exc, effects)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, registration, committed_effects=tuple(effects))


def registration_error(request, exc, effects):
    if effects:
        error = getattr(exc, "error", None) or CommandError(ErrorCode.CONFLICT, str(exc), RequestErrorDetails(None, str(exc)))
        return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error, tuple(effects))
    return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {"key", "title"})
    return WorkspaceEnsureRegistrationRequest(payload.get("key"), payload.get("title"))


def catalogue_entry():
    return replace(contributor_mutation_entry(WorkspaceEnsureRegistrationRequest, execute),
                   preparation=OperationPreparation(prepare))
