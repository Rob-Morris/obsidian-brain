"""Bounded literal search over lines and records in retained output."""
from dataclasses import dataclass, replace
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as reader_entry
from ..delivery_failure import DeliveryFailure
from ..results import ErrorCode, Ok, request_error


@dataclass(frozen=True, slots=True)
class ResultSearchPayload:
    """Resume with next_page/next_offset; coverage is derived from stored metadata.

    Coverage addresses retained result-body units, excluding canonical metadata
    and contextual extras on line/record views. scan_complete marks unit-view
    exhaustion; complete additionally requires no unsearched detail references.
    overlap_pages counts bounded look-behind I/O.
    """

    matches: tuple[dict[str, object], ...]
    scanned_pages: int
    next_page: int | None
    complete: bool
    next_offset: int | None = None
    scan_complete: bool = False
    details_skipped: bool = False
    coverage: str = 'retained-body-units'
    overlap_pages: int = 0


@dataclass(frozen=True, slots=True)
class ResultSearchRequest:
    """Search retained units without executing or refreshing the source command."""

    COMMAND_ID: ClassVar[str] = 'result.search'
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ResultSearchPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        'result_id': 'Owned retained invocation.',
        'literal': 'Literal; one hit per matching line/fragment/record.',
        'start_page': 'Source page to scan (1-based).',
        'start_offset': 'Unit offset in start_page (0-based); use next_offset.',
        'limit': 'Maximum matches (1–25); byte limits also apply.',
        'scan_pages': 'Forward pages (1–50); overlap adds at most 256 reads.',
    }
    result_id: str
    literal: str
    start_page: int = 1
    limit: int = 10
    scan_pages: int = 10
    start_offset: int = 0

    def __post_init__(self):
        if not isinstance(self.result_id, str) or not 1 <= len(self.result_id) <= 128:
            raise ValueError('result_id must contain 1–128 characters')
        if not isinstance(self.literal, str) or not 1 <= len(self.literal) <= 256:
            raise ValueError('literal must contain 1–256 characters')
        for name, minimum, maximum in (
            ('start_page', 1, 2**63 - 1),
            ('start_offset', 0, 2**63 - 1),
            ('limit', 1, 25),
            ('scan_pages', 1, 50),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{name} must be between {minimum} and {maximum}')


def execute(context, request):
    if context.result_delivery is None:
        return request_error(
            ResultSearchRequest, ErrorCode.CAPABILITY_UNAVAILABLE,
            'Select a result session before searching output.',
        )
    try:
        data = context.result_delivery.search(context, request)
    except DeliveryFailure as exc:
        return exc.navigation_error(request.COMMAND_ID, request.COMMAND_VERSION, request.result_id)
    except ValueError as exc:
        return request_error(ResultSearchRequest, ErrorCode.CONFLICT, str(exc))
    return Ok('result.search', 1, ResultSearchPayload(**data))


def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {
        'result_id', 'literal', 'start_page', 'start_offset', 'limit', 'scan_pages',
    })
    return ResultSearchRequest(**payload)


def catalogue_entry():
    return replace(reader_entry(ResultSearchRequest, execute),
                   summary='Search retained body units without rerunning the source.')
