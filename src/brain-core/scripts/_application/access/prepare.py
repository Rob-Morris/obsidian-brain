"""Prepare exact operation consent without entering the target operation."""
from dataclasses import dataclass, field
import json
from typing import ClassVar, Literal, Mapping

from .._response_budget import TextCursor, decode_text_cursor, encoded_result_size, validate_text_window
from ..access_contracts import PreparedOperation, PreparedOperationDetails
from ..receipts import CommittedEffect
from ..results import Ok
from ..types import validate_command_id
from ._support import control_entry, nonempty, object_fields
from .request import OperationConsent, validate_consent_request_size


@dataclass(frozen=True, slots=True)
class PrepareCommand:
    command_id: str
    arguments: Mapping[str, object]
    kind: Literal["operation"] = field(default="operation", init=False)
    def __post_init__(self):
        nonempty(self.command_id, "command_id")
        validate_command_id(self.command_id)
        def json_value(value):
            if isinstance(value, Mapping):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("target argument keys must be strings")
                return {key: json_value(item) for key, item in value.items()}
            if isinstance(value, list):
                return [json_value(item) for item in value]
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValueError("target arguments must contain only JSON values")
            return value
        if not isinstance(self.arguments, Mapping):
            raise ValueError("arguments must be an object")
        if "brain_operation" in self.arguments:
            raise ValueError("brain_operation is transport metadata, not a target argument")
        arguments = json_value(self.arguments)
        json.dumps(arguments, allow_nan=False)
        object.__setattr__(self, "arguments", arguments)



@dataclass(frozen=True, slots=True)
class InspectOperation:
    operation_id: str
    cursor: TextCursor | None = None
    max_characters: int = 12000
    kind: Literal["inspect"] = field(default="inspect", init=False)
    def __post_init__(self):
        nonempty(self.operation_id, "operation_id")
        validate_text_window(self.cursor, self.max_characters)


@dataclass(frozen=True, slots=True)
class AccessPrepareRequest:
    COMMAND_ID: ClassVar[str] = "access.prepare"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[object] = PreparedOperation | PreparedOperationDetails
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {"preparation": {"kind": "operation", "command_id": "artefact.delete", "arguments": {"path": "Thoughts/Test.md"}}}
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {"preparation": "Prepare a target command with its ordinary arguments, or inspect a context-owned preparation. This does not grant authorisation or enter the target."}
    preparation: PrepareCommand | InspectOperation
    def __post_init__(self):
        if not isinstance(self.preparation, (PrepareCommand, InspectOperation)):
            raise ValueError("invalid preparation variant")


def prepared_result(payload):
    """One complete review response for pre-persistence and outgoing byte checks."""
    return Ok(AccessPrepareRequest.COMMAND_ID, AccessPrepareRequest.COMMAND_VERSION, payload,
              committed_effects=(CommittedEffect("access.prepared", payload.operation_id),))


def execute(context, request):
    preparation = request.preparation
    if isinstance(preparation, PrepareCommand):
        payload = context.access.prepare(preparation.command_id, preparation.arguments)
        validate_consent_request_size(OperationConsent(payload.operation_id, payload.digest, payload.review))
        result = prepared_result(payload)
    else:
        payload = context.access.inspect(preparation.operation_id,
            cursor=preparation.cursor, max_characters=preparation.max_characters)
        result = Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)
    if isinstance(payload, PreparedOperation) and encoded_result_size(result) >= 8000:
        raise ValueError("Prepared review exceeds the 8000-byte response budget; prepare a smaller operation")
    return result


def decode(payload):
    object_fields(payload, {"preparation"}, {"preparation"})
    value = payload["preparation"]
    if not isinstance(value, Mapping):
        raise ValueError("preparation must be an object")
    if value.get("kind") == "operation":
        object_fields(value, {"kind", "command_id", "arguments"}, {"kind", "command_id", "arguments"})
        item = PrepareCommand(value["command_id"], value["arguments"])
    elif value.get("kind") == "inspect":
        object_fields(value, {"kind", "operation_id", "cursor", "max_characters"}, {"kind", "operation_id"})
        item = InspectOperation(value["operation_id"], decode_text_cursor(value.get("cursor")), value.get("max_characters", 12000))
    else:
        raise ValueError("preparation kind must be operation or inspect")
    return AccessPrepareRequest(item)


def catalogue_entry():
    return control_entry(AccessPrepareRequest, execute, mutation=True,
        summary="Prepare or inspect exact operation consent without entering the target operation.")
