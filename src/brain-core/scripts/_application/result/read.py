"""Typed result.read navigation over an authenticated retained invocation."""
from dataclasses import dataclass, replace
from typing import ClassVar, Literal, Mapping

from .._decoding import reject_unexpected
from .._read_support import catalogue_entry as reader_entry
from ..delivery_failure import DeliveryFailure
from ..results import ErrorCode, Ok, request_error


@dataclass(frozen=True, slots=True)
class ResultReadPayload:
    envelope: dict[str, object]


@dataclass(frozen=True, slots=True)
class ResultReadRequest:
    COMMAND_ID: ClassVar[str] = 'result.read'
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ResultReadPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        'result_id': 'Retained invocation ID; requires the owning result session.',
        'page': 'Absolute page; omit follows continuation. 0 all; +1/-1 relative.',
        'continuation': 'Previous delivery continuation; preserves view and selected extent.',
        'page_size': 'Requested lines/records up to the supported maximum; byte limits also apply.',
        'max_pages': 'Selection extent; all removes the query default of ten pages.',
        'detail': 'Bounded detail reference returned by a page; never a filesystem path.',
        'anchor': 'Zero-based source unit position when changing page size.',
    }
    result_id: str
    page: int | str | None = None
    continuation: dict[str, object] | None = None
    page_size: int | None = None
    max_pages: int | Literal['all'] | None = None
    detail: str | None = None
    anchor: int | None = None

    def __post_init__(self):
        if not isinstance(self.result_id, str) or not 1 <= len(self.result_id) <= 128:
            raise ValueError('result_id must contain 1–128 characters')
        if self.page is not None and type(self.page) is not int and self.page not in ('+1', '-1'):
            raise ValueError('page must be 0, a positive integer, or +1/-1')
        if type(self.page) is int and not 0 <= self.page <= 2**63 - 1:
            raise ValueError('page must be between 0 and 2^63-1')
        for name in ('page_size', 'max_pages'):
            value = getattr(self, name)
            if name == 'max_pages' and value == 'all':
                continue
            if value is not None and (type(value) is not int or not 1 <= value <= 2**63 - 1):
                raise ValueError(f'{name} must be a bounded positive integer')
        if self.anchor is not None and (type(self.anchor) is not int or not 0 <= self.anchor <= 2**63 - 1):
            raise ValueError('anchor must be a bounded non-negative integer')
        if self.detail is not None and (not isinstance(self.detail, str) or not 1 <= len(self.detail) <= 128):
            raise ValueError('detail must be a bounded reference')
        if self.continuation is not None and not isinstance(self.continuation, dict):
            raise ValueError('continuation must be an object')


def execute(context, request):
    if context.result_delivery is None:
        return request_error(ResultReadRequest, ErrorCode.CAPABILITY_UNAVAILABLE,
                             'Select a result session before retrieving output.')
    try:
        frame = context.result_delivery.read(context, request)
    except DeliveryFailure as exc:
        return exc.navigation_error(request.COMMAND_ID, request.COMMAND_VERSION, request.result_id)
    except ValueError as exc:
        return request_error(ResultReadRequest, ErrorCode.CONFLICT, str(exc))
    return Ok('result.read', 1, ResultReadPayload(frame))


def decode(payload: Mapping[str, object]):
    reject_unexpected(payload, {'result_id', 'page', 'continuation', 'page_size',
                                'max_pages', 'detail', 'anchor'})
    return ResultReadRequest(**payload)


def catalogue_entry():
    return replace(reader_entry(ResultReadRequest, execute),
                   summary='Read one page or detail of an owned invocation result.')
