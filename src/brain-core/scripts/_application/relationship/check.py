"""Bounded default integrity and explicitly selected semantic policies."""

from dataclasses import dataclass, field
from typing import ClassVar
from _relationships.policies import AssessmentPolicy, TranscriptPolicy
from .._decoding import reject_unexpected
from ..results import Ok, ErrorCode, request_error
from ._types import GraphResult, bounds
from .batch import decode_endpoint
from . import _reads


@dataclass(frozen=True, slots=True)
class RelationshipCheckRequest:
    COMMAND_ID: ClassVar[str] = "relationship.check"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    work_budget: int = field(default=5000, metadata={"description": "Maximum records in the explicit source integrity scan, from 1 to 5000."})
    assessment: AssessmentPolicy | None = field(default=None, metadata={"description": "Opt-in scoped assessment contract; omitted means no assessment obligation or positive verdict."})
    transcript: TranscriptPolicy | None = field(default=None, metadata={"description": "Opt-in confirmed persisted-transcript reciprocity scope; no type/status inference."})
    identities: tuple[str, ...] = field(default=(), metadata={"description": "Optional exact kind:id record scope, up to 100, for targeted integrity checks; this does not certify unselected records or policy standing."})
    cursor: str | None = field(default=None, metadata={"description": "Opaque source-scan continuation from a preceding check page. Each page reports its observed integrity; paged checks cannot certify whole-policy standing."})

    def __post_init__(self):
        bounds(100, self.work_budget)
        if not isinstance(self.identities, tuple) or len(self.identities) > 100:
            raise ValueError("identities must contain at most 100 kind:id selectors")


def decode(payload):
    reject_unexpected(payload, {"work_budget", "assessment", "transcript", "identities", "cursor"})
    assessment = payload.get("assessment")
    if assessment is not None:
        assessment = AssessmentPolicy(**{**assessment, "subject": decode_endpoint(assessment["subject"]), "criterion": decode_endpoint(assessment["criterion"])})
    transcript = payload.get("transcript")
    if transcript is not None:
        transcript = TranscriptPolicy(**{**transcript, "sources": tuple(decode_endpoint(item) for item in transcript["sources"]),
                                          "transcripts": tuple(decode_endpoint(item) for item in transcript["transcripts"])})
    return RelationshipCheckRequest(payload.get("work_budget", 5000), assessment, transcript,
                                    tuple(payload.get("identities", ())), payload.get("cursor"))


def read_result(context, request):
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    from _relationships.checks import inspect
    try:
        result = inspect(context.selected_brain.vault_root, require_fresh_compiled_router(context.selected_brain.vault_root),
                         budget=request.work_budget, assessment=request.assessment, transcript=request.transcript,
                         identities=request.identities, cursor=request.cursor)
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, GraphResult(
            tuple(result["records"]), tuple(result["findings"]), complete=result["complete"], visited=result["visited"],
            source_freshness=result["source_freshness"], continuation=result.get("cursor")))
    except (OSError, ValueError) as exc:
        return request_error(type(request), ErrorCode.CONFLICT, str(exc))


def catalogue_entry():
    return _reads.entry(RelationshipCheckRequest, read_result, "Check relationship integrity and explicitly scoped assessment or transcript policies against bounded source revisions.")
