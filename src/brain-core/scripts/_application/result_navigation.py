"""Pure, byte-aware result layout; storage and disclosure remain caller-owned."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from typing import Mapping

from _bootstrap.result_metadata import Delivery

CANONICAL_BODY_BYTES = 4000
ENCODED_BODY_BYTES = 12768


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def body_fits(value: object) -> bool:
    """Measure both representations, reserving the agreed metadata allocation."""
    text = _json(value)
    encoded = {"content": [
        {"type": "text", "text": text, "annotations": {"audience": ["assistant"]}},
        {"type": "text", "text": "", "annotations": {"audience": ["user"]}},
    ], "structuredContent": value, "isError": False}
    return (len(text.encode("utf-8")) <= CANONICAL_BODY_BYTES
            and len(json.dumps(encoded).encode("utf-8")) <= ENCODED_BODY_BYTES)


def _identity(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()[:32]


def _positive(value: int, name: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be an integer from 1 to {maximum or 'unbounded'}")
    return value


def _record_field(envelope: dict) -> str | None:
    payload = envelope.get("result")
    if not isinstance(payload, dict):
        return None
    if envelope.get("command") == "command.list" and isinstance(payload.get("entries"), list):
        return "entries"
    if envelope.get("command") in ("relationship.query", "history.query") and isinstance(payload.get("records"), list):
        return "records"
    return "items" if isinstance(payload.get("items"), list) else None


@dataclass(frozen=True, slots=True)
class Selection:
    start_page: int = 1
    max_pages: int | None = None

    def __post_init__(self) -> None:
        _positive(self.start_page, "start_page")
        if self.max_pages is not None:
            _positive(self.max_pages, "max_pages")

    def end(self, total_pages: int) -> int:
        return min(total_pages, self.start_page + self.max_pages - 1) if self.max_pages is not None else total_pages


def delivery_metadata(delivery: Delivery) -> dict:
    """Project the universal descriptor, including explicit inapplicable counts."""
    return asdict(delivery)


def project_delivery(envelope: dict, delivery: Delivery) -> dict:
    result = deepcopy(envelope)
    result["delivery"] = delivery_metadata(delivery)
    return result


def resolve_page(page: int | str, origin: int | None = None) -> int:
    """Resolve relative navigation only against an explicit validated origin."""
    if isinstance(page, str):
        if page not in ("+1", "-1") or origin is None:
            raise ValueError("relative page requires +1/-1 and an origin")
        _positive(origin, "origin")
        resolved = origin + int(page)
        if resolved < 1:
            raise ValueError("relative page is before page 1")
        return resolved
    if type(page) is not int or page < 0:
        raise ValueError("page must be a non-negative integer or +1/-1")
    return page


@dataclass(frozen=True, slots=True)
class ResultView:
    pages: tuple[dict, ...]
    details: Mapping[str, dict]
    unit: str
    page_size: int
    total_items: int
    view_id: str
    _envelope: dict = field(repr=False)
    _ranges: tuple[tuple[int, int], ...] = field(repr=False)
    requested_page_size: int | None = None
    source_lines: int | None = None
    fragments: int | None = None

    def metadata(self) -> dict:
        """Export constant-size JSON metadata; page bounds travel with each frame."""
        return view_metadata(self)

    def page(self, page: int = 1, start_page: int = 1, max_pages: int | None = None) -> dict:
        Selection(start_page, max_pages)
        number = start_page if resolve_page(page) == 0 else page
        frame = self.pages[number - 1] if 1 <= number <= len(self.pages) else self._envelope
        return select_page(frame, self.metadata(), page, start_page, max_pages)


def view_metadata(view: ResultView) -> dict:
    """Export view identity/counts independently of page bodies and detail maps."""
    return {"view_id": view.view_id, "unit": view.unit, "page_size": view.page_size,
            "requested_page_size": view.requested_page_size, "total_items": view.total_items,
            "total_pages": len(view.pages), "source_lines": view.source_lines,
            "fragments": view.fragments, "record_field": _record_field(view._envelope),
            "has_details": bool(view.details)}


def select_page(frame: dict, metadata: dict, page: int = 1, start_page: int = 1,
                max_pages: int | None = None) -> dict:
    """Apply selection to one stored frame, without reading other page bodies.

    The caller supplies the addressed frame (or an outcome-only frame for an
    empty/out-of-range view). Its delivery descriptor carries the unit bounds.
    """
    selected = Selection(start_page, max_pages)
    requested = resolve_page(page)
    number = selected.start_page if requested == 0 else requested
    total = metadata["total_pages"]
    end = selected.end(total)
    count = max(0, end - start_page + 1)
    valid = start_page <= number <= end or (total == 0 and number == start_page == 1)
    remaining = max(0, end - number) if valid else 0
    before = min(total, start_page - 1)
    after = max(0, total - max(end, start_page - 1))
    selection_id = _identity([metadata["view_id"], start_page, max_pages])
    def continuation(target: int, start: int = start_page) -> dict:
        return {"view_id": metadata["view_id"], "selection_id": _identity([metadata["view_id"], start, max_pages]),
                "page": target, "origin_page": number, "start_page": start, "max_pages": max_pages}
    observed = frame.get("delivery", {})
    bounds = (observed.get("first_item"), observed.get("last_item")) if valid and total else (None, None)
    descriptor = Delivery(
        count_scope=("ranked-candidate-window" if metadata.get("record_field") == "items"
                     and frame.get("command") in ("artefact.search", "resource.search")
                     else "produced-traversal-window" if metadata.get("record_field") == "records"
                     and frame.get("command") in ("relationship.query", "history.query")
                     else "produced-output"),
        status="out_of_range" if not valid else ("truncated" if remaining or before or after else "complete"),
        availability="available", view_id=metadata["view_id"], unit=metadata["unit"],
        requested_page_size=metadata.get("requested_page_size"), page_size=metadata["page_size"],
        page=number, first_item=bounds[0], last_item=bounds[1],
        total_items=metadata["total_items"], total_pages=total, source_lines=metadata.get("source_lines"),
        fragments=metadata.get("fragments"), selection_id=selection_id, start_page=start_page,
        max_pages=max_pages, end_page=end, selected_pages=count, remaining_pages=remaining,
        has_more=bool(remaining), pages_before_selection=before, pages_after_selection=after,
        has_more_outside_selection=bool(before or after),
        continuation=continuation(number + 1) if remaining else None,
        outside_continuation=continuation(end + 1, end + 1) if after and count else None,
        reason="page_out_of_range" if not valid else ("bounded_page" if remaining or before or after else None),
        message="Requested page is outside the current selection." if not valid else None,
    )
    result = deepcopy(frame)
    if not valid:
        result["result"] = None
    payload = result.get("result")
    record_field = _record_field(result)
    if valid and record_field is not None:
        if "returned" in payload:
            payload["returned"] = len(payload[record_field])
        if "truncated" in payload:
            payload["truncated"] = bool(payload["truncated"] or remaining or before or after)
    return project_delivery(result, descriptor)


def build_view(envelope: dict, page_size: int | None = None, unit: str | None = None) -> ResultView:
    """Lay out complete envelopes without executing queries or accessing storage.

    Content strings, items lists and command.list entries declare countable views.
    Structured roots retain their keys when compact detail references fit;
    otherwise explicit field pages preserve each component. Detail references
    address bounded envelopes, with next-detail links for multi-page fields.
    An explicit committed_effect_summary replaces automatic header lifting;
    full effects remain in the body and its detail views.
    """
    if not isinstance(envelope, dict):
        raise TypeError("envelope must be a dict")
    original = deepcopy(envelope)
    original.pop("delivery", None)
    payload = original.get("result")
    record_field = _record_field(original)
    detected = ("lines" if isinstance(payload, dict) and isinstance(payload.get("content"), str)
                else "records" if record_field is not None
                else "fields")
    if unit is not None and unit != detected:
        raise ValueError(f"unit must match the declared {detected} view")
    unit = detected
    size = _positive(page_size if page_size is not None else (100 if unit == "lines" else 25),
                     "page_size", 500 if unit == "lines" else 100)
    view_id = _identity([original, unit, size])
    details: dict[str, dict] = {}
    base = {key: value for key, value in original.items() if key != "result"}
    if (original.get("status") == "partial" and isinstance(payload, dict)
            and "committed_effects" in payload and "committed_effect_summary" not in base):
        base.setdefault("committed_effects", deepcopy(payload["committed_effects"]))

    if unit == "fields" and body_fits(payload):
        count = len(payload) if isinstance(payload, (dict, list)) else int(payload is not None)
        frames = ({**base, "result": payload, "delivery": {"first_item": 1, "last_item": count}},) if count else ()
        empty = {**base, "result": payload}
        view = ResultView(frames, {}, unit, size, count, view_id, empty,
                          ((1, count),) if count else (), page_size)
        return ResultView(tuple(view.page(i + 1) for i in range(len(frames))), {}, unit,
                          size, count, view_id, empty, view._ranges, page_size)

    def detail(value: object) -> dict:
        if isinstance(value, str):
            value = {"content": value}
        key = _identity([view_id, value])
        if key not in details:
            child = build_view({**base, "result": value}, page_size=1)
            details.update(child.details)
            for index, frame in enumerate(child.pages):
                address = key if index == 0 else f"{key}:{index + 1}"
                details[address] = deepcopy(frame)
                details[address]["delivery"]["detail_id"] = address
                details[address]["delivery"]["next_detail"] = f"{key}:{index + 2}" if index + 1 < len(child.pages) else None
        reference = {"detail_reference": key, "action": "result.read", "detail": key, "consistency": "immutable-output"}
        if isinstance(value, dict):
            names = list(value)
            reference["fields_available"] = names if body_fits({**reference, "fields_available": names}) else detail(names)
            if "id" in value:
                identity = value["id"]
                reference["record_identity"] = identity if body_fits({**reference, "record_identity": identity}) else detail(identity)
        return reference

    def bounded(value: object) -> object:
        if body_fits(value):
            return value
        if isinstance(value, str):
            return detail({"content": value})
        if isinstance(value, (dict, list)):
            # Field views preserve keys and indexes; no serialised JSON substitute.
            return detail(value)
        raise ValueError("mandatory scalar cannot fit the delivery budget")

    if unit == "fields" and isinstance(payload, dict):
        compact = {key: bounded(value) for key, value in payload.items()}
        # Aggregate overhead can exceed the budget even when each value fits.
        # Replace an actual component, never wrap the same oversized field again.
        for key in sorted(compact, key=lambda name: len(_json(compact[name])), reverse=True):
            if body_fits(compact):
                break
            value = compact[key]
            if len(_json(value)) > 512 and not (isinstance(value, dict) and "detail_reference" in value):
                compact[key] = detail(payload[key])
        if body_fits(compact):
            count = len(compact)
            frame = {**base, "result": compact, "delivery": {"first_item": 1, "last_item": count}}
            view = ResultView((frame,), details, unit, size, count, view_id,
                              {**base, "result": {}}, ((1, count),), page_size)
            return ResultView((view.page(),), details, unit, size, count, view_id,
                              view._envelope, view._ranges, page_size)

    source_lines = None
    fragments = None
    if unit == "lines":
        source = payload["content"].splitlines(keepends=True)
        source_lines = len(source)
        extras = {key: bounded(value) for key, value in payload.items() if key != "content"}
        largest_offset = max(map(len, source), default=0)
        probe = {"line": len(source), "start": largest_offset, "end": largest_offset,
                 "text": "𐀀", "line_ending": "\r\n\u2028", "line_complete": False}
        if not body_fits({"fragments": [probe], **extras}):
            reference = detail(extras)
            extras = {"context": {key: reference[key] for key in
                      ("detail_reference", "action", "detail", "consistency")}}
        units = []
        for number, line in enumerate(source, 1):
            ending = "\r\n" if line.endswith("\r\n") else line[-1:] if line[-1:] in ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029") else ""
            text = line[:-len(ending)] if ending else line
            offset = 0
            while True:
                def fragment(end: int) -> dict:
                    return {"line": number, "start": offset, "end": end, "text": text[offset:end],
                            "line_ending": ending if end == len(text) else "", "line_complete": end == len(text)}
                # Each character costs at least one canonical byte. Searching
                # beyond this ceiling repeatedly encodes the unbounded tail.
                lo, hi = offset, min(len(text), offset + CANONICAL_BODY_BYTES)
                while lo < hi:
                    middle = (lo + hi + 1) // 2
                    if body_fits({"fragments": [fragment(middle)], **extras}):
                        lo = middle
                    else:
                        hi = middle - 1
                if lo == offset and offset < len(text):
                    raise ValueError("text fragment metadata exceeds delivery budget")
                units.append(fragment(lo))
                offset = lo
                if offset == len(text):
                    break
        fragments = len(units)
        def body(batch: list) -> dict:
            result = {"fragments": batch, **extras}
            content = "".join(item["text"] + item["line_ending"] for item in batch)
            with_content = {"content": content, **result}
            return with_content if body_fits(with_content) else result
    elif unit == "records":
        units = [bounded(item) for item in payload[record_field]]
        extras = {key: bounded(value) for key, value in payload.items() if key != record_field}
        def body(batch: list) -> dict:
            result = {record_field: batch, **extras}
            if "returned" in result:
                result["returned"] = len(batch)
            return result
    else:
        if isinstance(payload, dict):
            units = [{"field": bounded(key), "value": bounded(value)} for key, value in payload.items()]
        elif isinstance(payload, list):
            units = [{"index": index, "value": bounded(value)} for index, value in enumerate(payload)]
        elif payload is None:
            units = []
        else:
            units = [{"value": bounded(payload)}]
        def body(batch: list) -> dict:
            return {"fields": batch, "root_type": type(payload).__name__}

    if unit in ("lines", "records") and not body_fits(body([])):
        extras = {"context": detail(extras)}

    batches: list[list] = []
    current: list = []
    for item in units:
        if current and (len(current) >= size or not body_fits(body([*current, item]))):
            batches.append(current)
            current = []
        if not body_fits(body([item])):
            # Large repeated contextual fields also need a single bounded reference.
            if unit in ("lines", "records") and extras:
                extras = {"context": detail(extras)}
            if not body_fits(body([item])):
                if unit == "fields" and isinstance(item, dict):
                    for component in sorted(item, key=lambda name: len(_json(item[name])), reverse=True):
                        if body_fits(body([item])):
                            break
                        if len(_json(item[component])) > 512:
                            item = {**item, component: detail(item[component])}
                else:
                    item = detail(item)
            if not body_fits(body([item])):
                raise ValueError("mandatory page metadata exceeds delivery budget")
        current.append(item)
    if current:
        batches.append(current)
    empty = {**base, "result": body([])}
    frames = tuple({**base, "result": body(batch)} for batch in batches)
    ranges = []
    position = 1
    for batch in batches:
        ranges.append((position, position + len(batch) - 1))
        position += len(batch)
    for frame, bounds in zip(frames, ranges):
        frame["delivery"] = {"first_item": bounds[0], "last_item": bounds[1]}
    view = ResultView(frames, details, unit, size, len(units), view_id, empty,
                      tuple(ranges), page_size, source_lines, fragments)
    return ResultView(tuple(view.page(index + 1) for index in range(len(frames))), details,
                      unit, size, len(units), view_id, empty, tuple(ranges), page_size,
                      source_lines, fragments)
