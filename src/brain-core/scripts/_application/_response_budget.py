"""Canonical response budgets and revision-bound Unicode text windows."""

from dataclasses import dataclass
import json
from typing import Mapping

from .results import ErrorCode, Ok, request_error


MODEL_TEXT_BUDGET = 16000
DEFAULT_TEXT_CHARACTERS = 12000
TEXT_WINDOW_DESCRIPTIONS = {
    "cursor": "Continuation from range.next_cursor; repeat the same resource reference and other selectors.",
    "max_characters": "Maximum Unicode characters (1-12000) after newline normalization; the byte budget may return fewer.",
}


def encoded_result_size(result) -> int:
    # Projection imports request contracts, so keep this dependency lazy.
    from .projection import canonical_result_envelope

    return len(json.dumps(canonical_result_envelope(result), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class TextCursor:
    revision: str
    offset: int

    def __post_init__(self):
        from _common import validate_document_revision

        validate_document_revision(self.revision)
        if not isinstance(self.offset, int) or isinstance(self.offset, bool) or self.offset < 0:
            raise ValueError("cursor offset must be a non-negative Unicode character offset")


@dataclass(frozen=True, slots=True)
class ContentRange:
    start: int
    end: int
    total_characters: int
    next_cursor: TextCursor | None


def validate_text_window(cursor, max_characters):
    if cursor is not None and not isinstance(cursor, TextCursor):
        raise ValueError("cursor must be a TextCursor")
    if (not isinstance(max_characters, int) or isinstance(max_characters, bool)
            or not 1 <= max_characters <= DEFAULT_TEXT_CHARACTERS):
        raise ValueError("max_characters must be between 1 and 12000")


def decode_text_cursor(value):
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"revision", "offset"}:
        raise ValueError("cursor requires exactly revision and offset")
    return TextCursor(value["revision"], value["offset"])


def bounded_text_result(request_type, content, revision, *, cursor, max_characters, payload,
                        byte_budget=MODEL_TEXT_BUDGET):
    """Return the largest requested prefix that fits, preserving source revision."""
    if cursor is not None and cursor.revision != revision:
        return request_error(request_type, ErrorCode.CONFLICT,
                             "The source changed between pages; restart this read without a cursor.", "cursor")
    start = 0 if cursor is None else cursor.offset
    if start > len(content):
        return request_error(request_type, ErrorCode.INVALID_REQUEST,
                             "The cursor offset is beyond the source text.", "cursor")

    def candidate(end):
        window = ContentRange(start, end, len(content),
                              TextCursor(revision, end) if end < len(content) else None)
        return Ok(request_type.COMMAND_ID, request_type.COMMAND_VERSION,
                  payload(content[start:end], window))

    low, high = start, min(len(content), start + max_characters)
    result = candidate(high)
    if encoded_result_size(result) <= byte_budget:
        return result
    # Search character boundaries; never cut UTF-8 or serialized JSON.
    while low < high:
        middle = (low + high + 1) // 2
        if encoded_result_size(candidate(middle)) <= byte_budget:
            low = middle
        else:
            high = middle - 1
    result = candidate(low)
    if (low == start and start < len(content)) or encoded_result_size(result) > byte_budget:
        return request_error(request_type, ErrorCode.INVALID_REQUEST,
                             "Response metadata exceeds the text page budget.", "cursor")
    return result
