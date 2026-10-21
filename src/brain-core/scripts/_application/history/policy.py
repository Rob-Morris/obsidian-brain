"""Discover effective automatic capture and bounded history usage."""

from dataclasses import dataclass, field
from typing import ClassVar
from _relationships.definitions import capture_policy
from _relationships.storage import Store
from .._decoding import reject_unexpected
from ..results import Ok, ErrorCode, request_error
from ..relationship._types import GraphResult, bounds
from ..relationship import _reads


@dataclass(frozen=True, slots=True)
class HistoryPolicyRequest:
    COMMAND_ID: ClassVar[str] = "history.policy"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    work_budget: int = field(default=5000, metadata={"description": "Maximum event records counted for usage; exhaustion reports incomplete counts."})

    def __post_init__(self):
        bounds(100, self.work_budget)


def decode(payload):
    reject_unexpected(payload, {"work_budget"})
    return HistoryPolicyRequest(**payload)


def read_result(context, request):
    store = Store(context.selected_brain.vault_root)
    try:
        rows = store.rows(kind="event", budget=request.work_budget)
        size = sum(store.path("event", row["id"]).stat().st_size for row in rows[:request.work_budget])
        result = {**capture_policy(store), "coverage": "supported Brain commands", "external_events_guaranteed": False,
                  "automatic_pruning": False, "events": min(len(rows), request.work_budget), "serialised_bytes": size}
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, GraphResult((result,), complete=len(rows) <= request.work_budget))
    except (ValueError, OSError) as exc:
        return request_error(type(request), ErrorCode.CONFLICT, str(exc))


def catalogue_entry():
    return _reads.entry(HistoryPolicyRequest, read_result, "Inspect effective history capture defaults and bounded event-count/byte usage without pruning.")
