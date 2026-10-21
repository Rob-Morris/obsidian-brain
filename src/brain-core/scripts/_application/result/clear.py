"""Explicit local cleanup of the caller's verified retained-result namespace."""
from dataclasses import dataclass, replace
from typing import ClassVar

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as reader_entry
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok, request_error
from ..types import EffectClass, InitialAuthorisationClass, RetryClass


@dataclass(frozen=True, slots=True)
class ResultClearPayload:
    cleared: bool


@dataclass(frozen=True, slots=True)
class ResultClearRequest:
    COMMAND_ID: ClassVar[str] = 'result.clear'
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ResultClearPayload


def execute(context, request):
    if context.result_delivery is None:
        return request_error(ResultClearRequest, ErrorCode.CAPABILITY_UNAVAILABLE,
                             'Select the result session to clear.')
    try:
        context.result_delivery.clear(context)
    except ValueError as exc:
        return request_error(ResultClearRequest, ErrorCode.CONFLICT, str(exc))
    return Ok('result.clear', 1, ResultClearPayload(True),
              committed_effects=(CommittedEffect('result.cleared', 'own-result-session'),))


def decode(payload):
    reject_unexpected(payload, set())
    return ResultClearRequest()


def catalogue_entry():
    entry = reader_entry(ResultClearRequest, execute)
    return replace(
        entry, initial_class=InitialAuthorisationClass.CONTROL,
        summary="Clear only the selected agent's retained-result namespace.",
        effect_class=EffectClass.CALLER_LOCAL_MUTATION, preparation=None,
        retry_class=RetryClass.RECEIPT_REQUIRED,
    )
