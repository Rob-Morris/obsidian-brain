"""Immutable operation bindings shared by consent and command owners.

Bindings describe an operation; they never confer authority. Domain owners
resolve their resources and call admission inside their existing effect guard.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Mapping, Protocol


BINDING_SCHEMA = "brain.operation-binding/1"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def content_digest(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class ObservedResource:
    kind: str
    identity: str
    revision: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind or not isinstance(self.identity, str) or not self.identity:
            raise ValueError("an observed resource requires kind and identity")
        if self.revision is not None and (not isinstance(self.revision, str) or not self.revision):
            raise ValueError("resource revision must be non-empty or absent")

    def to_wire(self) -> dict:
        return {"kind": self.kind, "identity": self.identity,
                "revision": self.revision}


@dataclass(frozen=True, slots=True)
class OperationBinding:
    command_id: str
    command_version: int
    request_json: str
    frozen_inputs_json: str
    observations: tuple[ObservedResource, ...]
    review_json: str
    schema: str = BINDING_SCHEMA

    def __post_init__(self) -> None:
        from .types import validate_command_id

        validate_command_id(self.command_id)
        if self.schema != BINDING_SCHEMA or type(self.command_version) is not int or self.command_version < 1:
            raise ValueError("unsupported operation binding identity")
        for value in (self.request_json, self.frozen_inputs_json, self.review_json):
            decoded = json.loads(value)
            if not isinstance(decoded, dict) or canonical_json(decoded) != value:
                raise ValueError("binding fields require canonical JSON objects")
        identities = [(item.kind, item.identity) for item in self.observations]
        if identities != sorted(set(identities)):
            raise ValueError("binding observations must have sorted unique identities")

    @property
    def frozen_inputs(self) -> dict:
        return json.loads(self.frozen_inputs_json)

    @property
    def review(self) -> dict:
        return json.loads(self.review_json)

    @property
    def digest(self) -> str:
        return content_digest(canonical_json(self.to_wire()))

    def to_wire(self) -> dict:
        return {
            "schema": self.schema,
            "command_id": self.command_id,
            "command_version": self.command_version,
            "request": json.loads(self.request_json),
            "frozen_inputs": self.frozen_inputs,
            "observations": [item.to_wire() for item in self.observations],
            "review": self.review,
        }

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "OperationBinding":
        if set(value) != {"schema", "command_id", "command_version", "request",
                          "frozen_inputs", "observations", "review"}:
            raise ValueError("invalid operation binding fields")
        observations = value["observations"]
        if not isinstance(observations, list):
            raise ValueError("binding observations must be an array")
        return cls(
            command_id=value["command_id"],
            command_version=value["command_version"],
            request_json=canonical_json(value["request"]),
            frozen_inputs_json=canonical_json(value["frozen_inputs"]),
            observations=tuple(ObservedResource(**item) for item in observations),
            review_json=canonical_json(value["review"]),
            schema=value["schema"],
        )


class PreparationSpec(Protocol):
    """A command-owned resolver, called only after target permission checks."""

    owner_guarded: bool

    def prepare(self, context, request, *, frozen_inputs=None) -> OperationBinding: ...


@dataclass(frozen=True, slots=True)
class OperationPreparation:
    """Attach a family planner to a catalogue entry without a dispatch table."""

    planner: Callable
    owner_guarded: bool = True

    def prepare(self, context, request, *, frozen_inputs=None) -> OperationBinding:
        return self.planner(context, request, frozen_inputs=frozen_inputs)


def request_value(request) -> dict:
    from .projection import canonical_wire_value

    return canonical_wire_value(request)


def _bounded_value(value):
    if isinstance(value, str) and len(value.encode("utf-8")) > 256:
        return {"sha256": content_digest(value), "bytes": len(value.encode("utf-8"))}
    if isinstance(value, dict):
        return {key: _bounded_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bounded_value(item) for item in value]
    return value


def bind_operation(request, *, observations=(), frozen_inputs=None, review=None):
    """Bind every typed argument, keeping large body bytes out of the descriptor."""
    original = request_value(request)
    arguments = _bounded_value(original)
    return OperationBinding(
        request.COMMAND_ID, request.COMMAND_VERSION,
        canonical_json({"sha256": content_digest(canonical_json(original)),
                        "arguments": arguments}), canonical_json(frozen_inputs or {}),
        tuple(sorted(observations, key=lambda item: (item.kind, item.identity))),
        canonical_json({"arguments": arguments, **(review or {})}),
    )


def live_query(context, request, *, frozen_inputs=None):
    """Authorise running this explicit query, not a frozen copy of its results."""
    del context, frozen_inputs
    return bind_operation(request, review={
        "operation": "Run the specified query over the selected Brain's live data.",
        "arguments": _bounded_value(request_value(request)),
    })


LIVE_QUERY = OperationPreparation(live_query, owner_guarded=False)


@dataclass(frozen=True, slots=True)
class ObservedRead:
    result: object
    observations: tuple[ObservedResource, ...]


def observe_document_read(context, result, content):
    from pathlib import Path
    from .results import Ok

    if not isinstance(result, Ok) or content is None:
        return result
    path = getattr(content, "source_path", None)
    if path is None:
        raise ValueError("persisted read did not identify its resolved source")
    relative = Path(path).relative_to(context.selected_brain.vault_root.resolve()).as_posix()
    return ObservedRead(result, (ObservedResource("document", relative, content.revision),))


def read_result_binding(context, request, *, result, frozen_inputs=None):
    from .projection import canonical_wire_value

    observations = result.observations if isinstance(result, ObservedRead) else ()
    value = result.result if isinstance(result, ObservedRead) else result
    payload = canonical_wire_value(value.result)
    identity = payload.get("path") or payload.get("resolved_path") or payload.get("reference")
    if identity is None:
        identity = canonical_json(_bounded_value(request_value(request)))
    return bind_operation(request, frozen_inputs=frozen_inputs,
                          observations=(*observations, ObservedResource("read-result", identity,
                                        content_digest(canonical_json(payload)))),
                          review={"read": identity, "revision": payload.get("revision")})


@dataclass(frozen=True, slots=True)
class ResultReadPreparation:
    """Bind a resolved, revision-bearing read and return the same admitted value."""

    reader: Callable
    owner_guarded: bool = True

    def prepare(self, context, request, *, frozen_inputs=None):
        from .results import Ok

        result = self.reader(context, request)
        value = result.result if isinstance(result, ObservedRead) else result
        if not isinstance(value, Ok):
            raise ValueError(value.error.message)
        return read_result_binding(context, request, result=result,
                                   frozen_inputs=frozen_inputs)


def execute_prepared_read(context, request, reader):
    from .results import Ok

    result = reader(context, request)
    value = result.result if isinstance(result, ObservedRead) else result
    if isinstance(value, Ok):
        admit_owner(context, request, read_result_binding, result=result)
    return value


def prepare_content(context, content, frozen_inputs=None):
    """Retain a staged source privately before a descriptor refers to it."""
    from ._mutation_support import InlineContent, resolve_mutation_content

    frozen = dict(frozen_inputs or {})
    if content is None:
        return "", frozen
    if not isinstance(content, InlineContent):
        source_key = "stage:" + content.handle
        if context.admission is None:
            raise ValueError("prepared staged content requires an owned consent context")
        if source_key not in frozen.get("pins", {}):
            from _staging import inspect_staged_body

            body = inspect_staged_body(context.selected_brain.vault_root, content.handle)
            pins = dict(frozen.get("pins", {}))
            pins[source_key] = context.admission.retain_content(source_key, body.encode("utf-8"))
            frozen["pins"] = pins
        pinned = context.admission.read_pinned(source_key)
        if pinned is None:
            raise ValueError("prepared content pin is unavailable; prepare the operation again")
        if content_digest(pinned) != frozen["pins"][source_key]["sha256"]:
            raise ValueError("prepared content pin changed; prepare the operation again")
        return pinned.decode("utf-8"), frozen
    body, _handle = resolve_mutation_content(context.selected_brain.vault_root,
                                              content, context=context)
    return body, frozen


def admit_owner(context, request, planner, **inputs) -> None:
    """Admit after domain validation, immediately before the first effect."""
    admission = context.admission
    if admission is None:
        # S1 foundation: production composition becomes mandatory at cutover.
        return
    binding = None
    if admission.requires_binding:
        binding = planner(context, request,
                          frozen_inputs=admission.frozen_inputs, **inputs)
    admission.admit(binding)
