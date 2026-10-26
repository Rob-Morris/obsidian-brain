"""Recover vault.check's structured findings through its owned delivery pages.

This is a machine consumer of the public result protocol, not a second check
executor. Text details in this contract are scalar finding fields; objects and
arrays retain their field/index coordinates. Unsupported or incomplete output
must fail rather than become a healthy, empty diagnosis.
"""

from __future__ import annotations

import json
from collections.abc import Callable


def complete_check_result(envelope: dict, read: Callable[[dict], dict]) -> dict:
    """Read immutable pages/details once, preserving the original result session."""
    def descriptor(frame):
        if not isinstance(frame, dict) or not isinstance(frame.get("delivery", {}), dict):
            raise ValueError("vault.check recovery returned an invalid delivery descriptor")
        delivery = frame.get("delivery", {})
        if delivery and (type(delivery.get("has_more")) is not bool
                         or type(delivery.get("total_items")) is not int
                         or delivery["total_items"] < 0
                         or "total_pages" not in delivery):
            raise ValueError("vault.check recovery returned an invalid delivery descriptor")
        if delivery.get("status") in ("unavailable", "out_of_range") or delivery.get("availability", "available") != "available":
            raise ValueError("vault.check recovery is unavailable; run brain vault check --json again")
        if (delivery.get("has_more_outside_selection") or delivery.get("pages_before_selection", 0)
                or delivery.get("pages_after_selection", 0) or delivery.get("start_page", 1) != 1):
            raise ValueError("vault.check recovery returned a restricted selection")
        if "total_pages" in delivery:
            total, page, remaining = (delivery.get(key) for key in ("total_pages", "page", "remaining_pages"))
            if (any(type(value) is not int for value in (total, page, remaining))
                    or total < 0 or not 1 <= page <= max(1, total)
                    or remaining != max(0, total - page)
                    or bool(delivery.get("has_more")) != bool(remaining)):
                raise ValueError("vault.check recovery returned contradictory page coverage")
        return delivery

    result_id = descriptor(envelope).get("result_id")
    cache: dict[str, object] = {}
    active: set[str] = set()

    def pages(first):
        seen = set()
        frame = first
        initial = descriptor(first)
        expected_page = 1
        while True:
            delivery = descriptor(frame)
            if any(delivery.get(key) != initial.get(key) for key in ("unit", "total_pages", "total_items")):
                raise ValueError("vault.check recovery changed its page totals")
            if delivery.get("page", expected_page) != expected_page:
                raise ValueError("vault.check recovery skipped or repeated a page")
            yield frame
            if not delivery.get("has_more"):
                return
            cursor = delivery.get("continuation")
            key = json.dumps(cursor, sort_keys=True)
            if cursor is None or key in seen:
                raise ValueError("vault.check recovery returned an incomplete or repeated continuation")
            seen.add(key)
            frame = read({"result_id": result_id, "continuation": cursor})
            expected_page += 1

    def resolve(value):
        if isinstance(value, dict):
            if value.get("action") == "result.read" and "detail_reference" in value:
                detail = value["detail_reference"]
                if not isinstance(detail, str) or detail in active:
                    raise ValueError("vault.check recovery returned an invalid or cyclic detail")
                if detail not in cache:
                    active.add(detail)
                    cache[detail] = materialise(read({"result_id": result_id, "detail": detail}))
                    active.remove(detail)
                return cache[detail]
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    def materialise(first):
        delivery = descriptor(first)
        if delivery.get("page", 1) != 1:
            raise ValueError("vault.check recovery did not start at the beginning")
        unit = delivery.get("unit")
        if unit in ("bytes", "lines"):
            chunks = []
            end = 0
            total = None
            line, offset = 1, 0
            legacy = "fragments" in first["result"]
            complete_line = True
            for frame in pages(first):
                payload = frame["result"]
                if ("fragments" in payload) != legacy:
                    raise ValueError("vault.check recovery changed its text representation")
                if legacy:
                    for fragment in payload["fragments"]:
                        if (fragment["line"] != line or fragment["start"] != offset
                                or fragment["end"] != offset + len(fragment["text"])):
                            raise ValueError("vault.check recovery returned incomplete legacy text")
                        chunks.append(fragment["text"] + fragment["line_ending"])
                        offset = fragment["end"]
                        complete_line = fragment["line_complete"]
                        if complete_line:
                            line, offset = line + 1, 0
                    continue
                text, span = payload["content"], payload["content_range"]
                if total is None:
                    total = span["total"]
                if not isinstance(text, str) or span["start"] != end or span["end"] != end + len(text):
                    raise ValueError("vault.check recovery returned incomplete text")
                if span["total"] != total:
                    raise ValueError("vault.check recovery changed its text total")
                end = span["end"]
                chunks.append(text)
            if (total is not None and end != total) or (legacy and not complete_line):
                raise ValueError("vault.check recovery returned truncated text")
            return "".join(chunks)

        payload = first["result"]
        if isinstance(payload, dict) and set(payload) == {"fields", "root_type"}:
            root_type = payload["root_type"]
            if root_type not in ("list", "dict"):
                raise ValueError("vault.check recovery returned unsupported fields")
            values = [] if root_type == "list" else {}
            for frame in pages(first):
                if frame["result"]["root_type"] != root_type:
                    raise ValueError("vault.check recovery changed its field type")
                for item in frame["result"]["fields"]:
                    if isinstance(values, list):
                        if item["index"] != len(values):
                            raise ValueError("vault.check recovery returned missing or repeated indexes")
                        values.append(resolve(item["value"]))
                    else:
                        key = resolve(item["field"])
                        if key in values:
                            raise ValueError("vault.check recovery returned repeated fields")
                        values[key] = resolve(item["value"])
            total = first["delivery"]["total_items"]
            if len(values) != total:
                raise ValueError("vault.check recovery returned incomplete fields")
            return values
        if first.get("delivery", {}).get("has_more"):
            raise ValueError("vault.check recovery returned an unsupported paged shape")
        if delivery:
            count = len(payload) if isinstance(payload, (dict, list)) else int(payload is not None)
            if delivery["total_pages"] not in (0, 1) or delivery["total_items"] != count:
                raise ValueError("vault.check recovery returned incomplete compact fields")
        return resolve(payload)

    try:
        result = materialise(envelope)
        if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
            raise ValueError("vault.check recovery did not return complete findings")
        if any(not isinstance(finding, dict) for finding in result["findings"]):
            raise ValueError("vault.check recovery returned unsupported findings")
        return result
    except (KeyError, TypeError) as exc:
        raise ValueError("vault.check recovery returned an unsupported result shape") from exc
