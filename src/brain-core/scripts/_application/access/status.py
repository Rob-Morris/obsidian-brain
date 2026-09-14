"""Bounded owner-local authorisation inventory and canonical command review."""
from dataclasses import dataclass
from typing import ClassVar

from ..access_contracts import AccessPageCursor, AccessStatusPayload, AccessStatusView
from .._response_budget import encoded_result_size, MODEL_TEXT_BUDGET
from ..results import Ok
from ..types import validate_command_id
from ._support import control_entry, object_fields, nonempty


@dataclass(frozen=True, slots=True)
class AccessStatusRequest:
    COMMAND_ID: ClassVar[str] = "access.status"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = AccessStatusPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {"target_command_id": "Optional exact command; returns canonical command_review for explicit blanket consent.", "view": "List grants, prepared operations, or initial command authorisation.", "cursor": "Repeat the same view and command filter with the returned continuation.", "page_size": "Maximum rows, 1–64; the byte budget can return fewer."}
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {"target_command_id": "artefact.delete"}
    view: AccessStatusView = AccessStatusView.GRANTS
    target_command_id: str | None = None
    cursor: AccessPageCursor | None = None
    page_size: int = 16

    def __post_init__(self):
        if not isinstance(self.view, AccessStatusView):
            raise ValueError("invalid access status view")
        if self.target_command_id is not None:
            nonempty(self.target_command_id, "target_command_id")
            validate_command_id(self.target_command_id)
        if self.cursor is not None and not isinstance(self.cursor, AccessPageCursor):
            raise ValueError("invalid access status cursor")
        if isinstance(self.page_size, bool) or not isinstance(self.page_size, int) or not 1 <= self.page_size <= 64:
            raise ValueError("page_size must be between 1 and 64")


def execute(context, request):
    size = request.page_size
    while True:
        payload = context.access.status(view=request.view, command_id=request.target_command_id,
            cursor=request.cursor, page_size=size)
        result = Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)
        if encoded_result_size(result) < MODEL_TEXT_BUDGET:
            return result
        if size == 1:
            raise ValueError("A status entry exceeds the response budget")
        size = max(1, size // 2)


def decode(payload):
    object_fields(payload, {"view", "target_command_id", "cursor", "page_size"})
    cursor = payload.get("cursor")
    if cursor is not None:
        object_fields(cursor, {"revision", "after", "scope"}, {"revision", "after", "scope"})
        cursor = AccessPageCursor(**cursor)
    return AccessStatusRequest(AccessStatusView(payload.get("view", "grants")),
        payload.get("target_command_id"), cursor, payload.get("page_size", 16))


def catalogue_entry():
    return control_entry(AccessStatusRequest, execute,
        summary="Inspect permissions, current authorisation and canonical command consent reviews.")
