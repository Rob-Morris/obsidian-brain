"""Typed retry-safe ``attachment.upload`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok


class AttachmentDestinationKind(str, Enum):
    ARTEFACT = "artefact"
    FOLDER = "folder"


@dataclass(frozen=True, slots=True)
class AttachmentDestination:
    kind: AttachmentDestinationKind
    key: str
    folder: str


@dataclass(frozen=True, slots=True)
class AttachmentUploadPayload:
    destination: AttachmentDestination
    path: str
    embed: str
    bytes: int
    sha256: str
    created: bool
    would_create: bool
    dry_run: bool


@dataclass(frozen=True, slots=True)
class AttachmentUploadRequest:
    COMMAND_ID: ClassVar[str] = "attachment.upload"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = AttachmentUploadPayload

    destination_key: str
    name: str
    content_base64: str

    def __post_init__(self) -> None:
        for field in ("destination_key", "name"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"attachment.upload {field} must be a non-empty string"
                )
        if not isinstance(self.content_base64, str):
            raise ValueError("attachment.upload content_base64 must be a string")


def execute(context: InvocationContext, request: AttachmentUploadRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    import upload_attachment

    vault_root = str(context.selected_brain.vault_root)
    try:
        router = None
        if upload_attachment.attachment_destination_requires_router(
            request.destination_key
        ):
            router = load_fresh_compiled_router(vault_root)
            if "error" in router:
                return no_effect_error(
                    AttachmentUploadRequest,
                    ErrorCode.CONFLICT,
                    router["error"],
                )
        destination = upload_attachment.resolve_attachment_destination(
            router,
            request.destination_key,
        )
        filename = upload_attachment.validate_attachment_name(request.name)
        content = upload_attachment.decode_attachment_base64(
            request.content_base64
        )
    except ValueError as exc:
        return no_effect_error(
            AttachmentUploadRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
        )

    try:
        with vault_mutation_lock(vault_root):
            if upload_attachment.attachment_destination_requires_router(request.destination_key):
                router = load_fresh_compiled_router(vault_root)
                if "error" in router:
                    raise ValueError(router["error"])
            plan = upload_attachment.plan_attachment_upload(
                vault_root,
                router,
                destination_key=request.destination_key,
                name=filename,
                content=content,
            )
            from ..preparation import admit_owner

            admit_owner(context, request, attachment_binding, plan=plan)
            result = ({"destination": plan.destination, "path": plan.path,
                       "embed": plan.embed, "bytes": plan.bytes, "sha256": plan.sha256,
                       "created": False, "would_create": plan.would_create}
                      if context.dry_run else upload_attachment.apply_attachment_upload(vault_root, plan))
    except MutationLockError as exc:
        return no_effect_error(
            AttachmentUploadRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except FileExistsError as exc:
        return no_effect_error(
            AttachmentUploadRequest,
            ErrorCode.CONFLICT,
            str(exc),
            "name",
        )
    except ValueError as exc:
        return no_effect_error(
            AttachmentUploadRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
        )
    payload = _payload(result, dry_run=context.dry_run)
    effects = (
        (CommittedEffect("attachment.created", payload.path),)
        if payload.created
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def _payload(result, *, dry_run: bool) -> AttachmentUploadPayload:
    destination = result["destination"]
    return AttachmentUploadPayload(
        destination=AttachmentDestination(
            AttachmentDestinationKind(destination["kind"]),
            destination["key"],
            destination["folder"],
        ),
        path=result["path"],
        embed=result["embed"],
        bytes=result["bytes"],
        sha256=result["sha256"],
        created=bool(result["created"]),
        would_create=bool(result.get("would_create", result["created"])),
        dry_run=dry_run,
    )


def decode(payload: Mapping[str, object]) -> AttachmentUploadRequest:
    allowed = {"destination_key", "name", "content_base64"}
    reject_unexpected(payload, allowed)
    values = {field: payload.get(field) for field in allowed}
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError(
            "destination_key, name and content_base64 must be strings"
        )
    return AttachmentUploadRequest(
        destination_key=values["destination_key"],
        name=values["name"],
        content_base64=values["content_base64"],
    )


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import OperationPreparation

    return replace(contributor_mutation_entry(AttachmentUploadRequest, execute),
                   preparation=OperationPreparation(prepare))


def attachment_binding(context, request, *, plan, frozen_inputs=None):
    from ..preparation import ObservedResource, bind_operation, canonical_json, content_digest

    return bind_operation(request, observations=(
        ObservedResource("attachment", plan.path, None if plan.would_create else "sha256:" + plan.sha256),
        ObservedResource("destination", plan.path, content_digest(canonical_json(plan.destination))),
        ObservedResource("content", plan.path, "sha256:" + plan.sha256),
    ), frozen_inputs=frozen_inputs, review={"path": plan.path, "bytes": plan.bytes,
                                           "sha256": plan.sha256, "would_create": plan.would_create})


def prepare(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    import upload_attachment

    root = str(context.selected_brain.vault_root)
    with vault_mutation_lock(root):
        router = None
        if upload_attachment.attachment_destination_requires_router(request.destination_key):
            router = load_fresh_compiled_router(root)
            if "error" in router:
                raise ValueError(router["error"])
        plan = upload_attachment.plan_attachment_upload(
            root, router, destination_key=request.destination_key, name=request.name,
            content=upload_attachment.decode_attachment_base64(request.content_base64))
        return attachment_binding(context, request, plan=plan, frozen_inputs=frozen_inputs)
