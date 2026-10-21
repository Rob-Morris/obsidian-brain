"""Behavioural contracts for pure result page layouts."""
import json

import pytest

from _application.result_navigation import (
    Delivery, body_fits, build_view, delivery_metadata, resolve_page,
    select_page, view_metadata, ENCODED_BODY_BYTES,
)


def envelope(result):
    return {"status": "ok", "command": "test.read", "result": result,
            "committed_effects": [{"path": "Example.md"}]}


def recover_detail_text(view, value):
    if isinstance(value, str):
        return value
    address = value["detail_reference"]
    text = ""
    while address:
        frame = view.details[address]
        payload = frame["result"]
        text += ("".join(item["text"] + item["line_ending"] for item in payload["fragments"])
                 if "fragments" in payload else payload["content"])
        address = frame["delivery"]["next_detail"]
    return text


def test_records_exact_union_and_projection_parity():
    rows = [{"id": i, "text": "𐐀\\\"" * (i % 40)} for i in range(163)]
    view = build_view(envelope({"items": rows}))
    assert [row for page in view.pages for row in page["result"]["items"]] == rows
    assert view.total_items == len(rows)
    assert view.view_id == build_view(envelope({"items": rows})).view_id
    for number, frame in enumerate(view.pages, 1):
        assert frame == view.page(number)
        assert frame["delivery"]["total_pages"] == len(view.pages)
        assert body_fits(frame["result"])
        assert json.loads(json.dumps(frame)) == frame
        assert frame["committed_effects"] == envelope(None)["committed_effects"]


def test_selection_get_all_and_last_page_continuation():
    view = build_view(envelope({"items": list(range(12))}), page_size=1)
    first = view.page(0, start_page=2, max_pages=3)
    info = first["delivery"]
    assert first["result"]["items"] == [1]
    assert info["selected_pages"] == 3
    assert info["remaining_pages"] == 2
    assert info["pages_before_selection"] == 1
    assert info["pages_after_selection"] == 8
    last = view.page(4, start_page=2, max_pages=3)["delivery"]
    assert not last["has_more"]
    assert last["continuation"] is None
    assert last["outside_continuation"]["start_page"] == 5
    assert last["outside_continuation"]["max_pages"] == 3
    assert last["outside_continuation"]["selection_id"] != last["selection_id"]


def test_numeric_selection_reapplied_to_growth_and_shrink():
    for count, selected, outside in [(6, 6, 0), (12, 10, 2)]:
        info = build_view(envelope({"items": list(range(count))}), 1).page(0, max_pages=10)["delivery"]
        assert (info["selected_pages"], info["pages_after_selection"]) == (selected, outside)
    result = build_view(envelope({"items": [1]}), 1).page(5, start_page=5, max_pages=10)
    assert result["delivery"]["status"] == "out_of_range"
    assert result["delivery"]["total_pages"] == 1
    assert result["result"] is None
    assert result["status"] == "ok"


@pytest.mark.parametrize("payload", [{"items": []}, {"content": ""}, {}])
def test_empty_accessible_set(payload):
    view = build_view(envelope(payload))
    assert view.pages == ()
    for page in (0, 1):
        info = view.page(page)["delivery"]
        assert info["status"] == "complete"
        assert info["total_pages"] == info["total_items"] == 0
        assert info["continuation"] is None


def test_long_unicode_line_exact_reconstruction_size_one():
    text = "a𐐀\\\"" * 15000 + "\r\nshort\nlast\u2028tail"
    view = build_view(envelope({"content": text}), 1)
    pieces = [piece for page in view.pages for piece in page["result"]["fragments"]]
    assert "".join(piece["text"] + piece["line_ending"] for piece in pieces) == text
    assert view.source_lines == 4
    assert view.fragments == view.total_items == len(pieces) == len(view.pages)
    for previous, following in zip(pieces, pieces[1:]):
        if previous["line"] == following["line"]:
            assert previous["end"] == following["start"]
    assert all(body_fits(page["result"]) for page in view.pages)


def test_structured_details_retain_every_field_and_make_progress():
    original = {"name": "record", "body": "巨大𐐀" * 10000,
                "nested": {"values": list(range(2000))}, "extra": False}
    view = build_view(envelope({"items": [original]}), 1)
    reference = view.pages[0]["result"]["items"][0]["detail_reference"]
    frame = view.details[reference]
    assert body_fits(frame["result"])
    compact = frame["result"]
    assert set(compact) == set(original)
    assert compact["name"] == original["name"]
    assert compact["extra"] is False
    body_reference = compact["body"]["detail_reference"]
    text = ""
    while body_reference:
        frame = view.details[body_reference]
        text += "".join(piece["text"] + piece["line_ending"] for piece in frame["result"]["fragments"])
        body_reference = frame["delivery"]["next_detail"]
    assert text == original["body"]
    assert all(body_fits(frame["result"]) for frame in view.details.values())


def test_small_structured_root_preserves_original_shape():
    original = {"path": "Example.md", "one": 1, "two": [False, None]}
    view = build_view(envelope(original), 1)
    assert len(view.pages) == 1
    assert view.page()["result"] == original


def test_oversized_root_keeps_small_fields_without_json_dump():
    original = {"one": 1, "two": "x" * 10000}
    view = build_view(envelope(original), 1)
    compact = view.page()["result"]
    assert set(compact) == set(original)
    assert compact["one"] == 1
    assert compact["two"]["detail_reference"] in view.details


@pytest.mark.parametrize("size", [True, False, 0, -1, 1.5, 101, "25"])
def test_invalid_record_sizes(size):
    with pytest.raises(ValueError):
        build_view(envelope({"items": []}), size)


def test_line_limits_and_declared_units():
    assert build_view(envelope({"content": "x"})).page_size == 100
    assert build_view(envelope({"content": "x"}), 500).page_size == 500
    with pytest.raises(ValueError):
        build_view(envelope({"content": "x"}), 501)
    with pytest.raises(ValueError):
        build_view(envelope([1, 2]), unit="records")


def test_relative_pages_require_origin_and_reject_bool():
    assert resolve_page("+1", 3) == 4
    assert resolve_page("-1", 3) == 2
    for page, origin in [("+1", None), ("-1", 1), (True, None), ("2", 1)]:
        with pytest.raises(ValueError):
            resolve_page(page, origin)


def test_failure_descriptor_has_no_private_counts():
    info = delivery_metadata(Delivery(reason="inaccessible"))
    assert info["availability"] == "unavailable"
    assert info["total_pages"] is info["total_items"] is info["selected_pages"] is None


def test_no_input_or_returned_page_mutation():
    original = envelope({"items": [{"id": 1}]})
    view = build_view(original)
    frame = view.page()
    frame["result"]["items"][0]["id"] = 2
    assert view.page()["result"]["items"][0]["id"] == 1
    assert original["result"]["items"][0]["id"] == 1


def test_partial_effects_remain_visible_on_every_page():
    view = build_view({"status": "partial", "result": {
        "committed_effects": [{"path": "written.md"}], "context": "x" * 10000}}, 1)
    assert all(page["status"] == "partial" for page in view.pages)
    assert all(page["committed_effects"] == [{"path": "written.md"}] for page in view.pages)


def test_empty_content_context_is_bounded_and_accessible():
    view = build_view(envelope({"content": "", **{str(i): "x" * 1000 for i in range(20)}}))
    frame = view.page()
    assert body_fits(frame["result"])
    assert frame["result"]["context"]["detail_reference"] in view.details


@pytest.mark.parametrize("page,start,limit", [(True, 1, None), (1, True, None), (1, 1, True), (1, 1, 0)])
def test_selection_rejects_boolean_and_invalid_controls(page, start, limit):
    with pytest.raises(ValueError):
        build_view(envelope({"items": [1]})).page(page, start, limit)


def test_get_all_never_bundles_pages_and_size_changes_view():
    source = envelope({"items": list(range(101))})
    view = build_view(source, 100)
    frame = view.page(0)
    assert frame["result"]["items"] == list(range(100))
    assert frame["delivery"]["status"] == "truncated"
    assert frame["delivery"]["total_pages"] == 2
    assert build_view(source, 25).view_id != view.view_id
    assert build_view(envelope({"items": [1]})).page(0)["delivery"]["status"] == "complete"


def test_variable_record_bytes_determine_exact_page_count():
    records = [{"id": i, "text": "𐐀" * 300} for i in range(20)]
    view = build_view(envelope({"items": records}), 100)
    assert len(view.pages) > 1
    assert view.page()["delivery"]["total_pages"] == len(view.pages)
    assert [item for frame in view.pages for item in frame["result"]["items"]] == records
    assert view.pages[-1]["delivery"]["last_item"] == len(records)


def test_oversized_identity_and_field_names_have_accessible_details():
    row = {"id": "𐐀" * 3000, "field" * 3000: "value", "body": "x" * 5000}
    view = build_view(envelope({"items": [row]}), 1)
    assert view.details
    assert all(body_fits(frame["result"]) for frame in (*view.pages, *view.details.values()))


def test_actual_proxy_ascii_outer_encoding_caps_emoji():
    from brain_mcp._result_content import result_text_wire

    def actual_encoded(body):
        return len(json.dumps({"content": result_text_wire("", body),
                               "structuredContent": body, "isError": False}).encode("utf-8"))
    oversized = {"content": "😀" * 700}
    assert len(json.dumps(oversized, ensure_ascii=False).encode("utf-8")) < 4000
    assert actual_encoded(oversized) > ENCODED_BODY_BYTES
    assert not body_fits(oversized)
    view = build_view(envelope({"content": ("😀\\\"" * 5000)}), 1)
    assert all(actual_encoded(frame["result"]) <= ENCODED_BODY_BYTES for frame in view.pages)
    for frame in view.pages:
        body = frame["result"]
        text = "".join(item["text"] + item["line_ending"] for item in body["fragments"])
        if body_fits({**body, "content": text}):
            assert body["content"] == text
    assert build_view(envelope({"content": "short 😀"})).page()["result"]["content"] == "short 😀"


def test_record_page_owned_counts_and_cursor_scope():
    view = build_view(envelope({"items": [1, 2, 3], "returned": 3,
                                "truncated": False, "next_cursor": "owner-cursor"}), 2)
    assert [frame["result"]["returned"] for frame in view.pages] == [2, 1]
    assert [frame["result"]["truncated"] for frame in view.pages] == [True, False]
    assert all(frame["result"]["next_cursor"] == "owner-cursor" for frame in view.pages)
    empty = {"items": [], "returned": 0, "truncated": False, "next_cursor": None}
    view = build_view(envelope(empty))
    assert view.page()["result"] == empty
    assert view.page()["delivery"]["total_pages"] == 0


def test_serialised_metadata_and_one_frame_reconstruct_selection():
    view = build_view(envelope({"items": list(range(20))}), 2)
    metadata = json.loads(json.dumps(view_metadata(view)))
    assert metadata == view.metadata()
    assert "pages" not in metadata and "ranges" not in metadata
    frame = json.loads(json.dumps(view.pages[3]))
    assert select_page(frame, metadata, 4, 2, 3) == view.page(4, 2, 3)
    assert select_page(frame, metadata, 0, 4, 2) == view.page(0, 4, 2)
    assert select_page(frame, metadata, 11)["delivery"] == view.page(11)["delivery"]
    assert select_page(frame, metadata, 11)["result"] is None


def test_nested_actual_page_does_not_recurse_through_field_wrappers():
    source = build_view(envelope({"items": [{"id": 1, "body": "x" * 3500}]})).page()
    assert len(json.dumps(source).encode()) > 4000
    view = build_view(envelope({"envelope": source}))
    assert set(view.page()["result"]) == {"envelope"}
    assert all(body_fits(frame["result"]) for frame in (*view.pages, *view.details.values()))
    assert len(view.details) < 30


def test_near_budget_key_and_value_route_components_and_preserve_all_fields():
    original = {"k" * 2100: "v" * 1850, "availability_freshness": "current"}
    assert body_fits(original["k" * 2100])
    view = build_view(envelope(original), 1)
    compact = view.page()["result"]
    assert set(compact) == set(original)
    assert compact["availability_freshness"] == "current"
    assert recover_detail_text(view, compact["k" * 2100]) == original["k" * 2100]
    assert all(body_fits(frame["result"]) for frame in (*view.pages, *view.details.values()))


def test_field_pages_still_progress_when_compact_keys_cannot_fit():
    original = {("key" * 700) + str(i): "v" * 1850 for i in range(4)}
    view = build_view(envelope(original), 1)
    assert len(view.pages) == len(original)
    assert all("fields" in frame["result"] for frame in view.pages)
    fields = [item for frame in view.pages for item in frame["result"]["fields"]]
    assert {recover_detail_text(view, item["field"]) for item in fields} == set(original)
    assert {recover_detail_text(view, item["field"]): recover_detail_text(view, item["value"])
            for item in fields} == original
    assert all(body_fits(frame["result"]) for frame in (*view.pages, *view.details.values()))
    assert len(view.details) < 30


def test_large_inventory_keeps_availability_and_declares_real_read_action():
    view = build_view(envelope({"availability_freshness": "observed-projection-only",
                                "providers": [{"name": str(i), "body": "x" * 3000} for i in range(5)]}))
    result = view.page()["result"]
    assert result["availability_freshness"] == "observed-projection-only"
    reference = result["providers"]
    assert reference["action"] == "result.read"
    assert reference["detail"] == reference["detail_reference"]
    assert reference["detail"] in view.details


def test_command_list_entries_are_records_with_original_catalogue_metadata():
    entries = [{"command_id": f"test.command-{i}", "summary": "😀\\\"" * 50} for i in range(92)]
    extras = {"catalogue_id": "catalogue-current", "snapshot_token": "snapshot-1",
              "availability_freshness": "observed", "next_cursor": {"command_id": "last", "snapshot_token": "snapshot-1"},
              "detail": "summary", "returned": len(entries), "truncated": False}
    source = {**envelope({"entries": entries, **extras}), "command": "command.list"}
    view = build_view(source)
    assert view.unit == "records" and view.page_size == 25
    assert view.total_items == len(entries)
    assert [entry for frame in view.pages for entry in frame["result"]["entries"]] == entries
    assert view.metadata()["record_field"] == "entries"
    assert len(view.pages) > 4  # Byte-aware boundaries, not ceil(92 / 25).
    for number, frame in enumerate(view.pages, 1):
        payload = frame["result"]
        assert "items" not in payload
        assert payload["returned"] == len(payload["entries"])
        assert payload["truncated"] == (number < len(view.pages))
        assert frame["delivery"]["total_pages"] == len(view.pages)
        assert body_fits(payload)
        for key in set(extras) - {"returned", "truncated"}:
            assert payload[key] == extras[key]
        assert select_page(frame, view.metadata(), number) == frame


def test_empty_command_catalogue_retains_entries_and_cursor_shape():
    payload = {"entries": [], "catalogue_id": "empty", "snapshot_token": "snapshot",
               "next_cursor": None, "availability_freshness": "current"}
    view = build_view({**envelope(payload), "command": "command.list"})
    assert view.unit == "records" and view.total_items == 0 and view.pages == ()
    assert view.page()["result"] == payload
    assert view.page()["delivery"]["total_pages"] == 0


def test_entries_detection_is_scoped_to_command_catalogue():
    payload = {"entries": [{"id": 1}], "traversal": {"complete": False}}
    view = build_view({**envelope(payload), "command": "relationship.query"})
    assert view.unit == "fields"
    assert view.page()["result"] == payload
    assert view.metadata()["record_field"] is None


@pytest.mark.parametrize('command,field', [('command.list', 'entries'),
    ('artefact.list', 'items'), ('artefact.search', 'items')])
@pytest.mark.parametrize('source_truncated', [False, True])
def test_native_window_truncation_is_preserved_on_last_delivery_page(command, field, source_truncated):
    cursor = {'source': 'next-window'} if source_truncated else None
    source = {**envelope({field: list(range(30)), 'truncated': source_truncated,
                         'returned': 30, 'next_cursor': cursor}), 'command': command}
    view = build_view(source, page_size=10)
    assert len(view.pages) == 3
    for number, page in enumerate(view.pages, 1):
        assert page['result']['truncated'] is (source_truncated or number < 3)
        assert page['result']['next_cursor'] == cursor
        assert page['result']['returned'] == 10


def test_long_line_fragmentation_bounds_encoding_work(monkeypatch):
    from _application import result_navigation as navigation

    original = navigation.body_fits
    work = []

    def measured(value):
        work[-1] += len(json.dumps(value, ensure_ascii=False))
        return original(value)

    monkeypatch.setattr(navigation, 'body_fits', measured)
    for length in (100_000, 800_000):
        work.append(0)
        text = 'x' * length
        view = navigation.build_view(envelope({'content': text}))
        assert ''.join(fragment['text'] for page in view.pages
                       for fragment in page['result']['fragments']) == text
        assert all(original(page['result']) for page in view.pages)
    # An eightfold longer line needs proportional bounded fragment probes,
    # rather than repeatedly encoding its entire remaining suffix.
    assert work[1] < 10 * work[0]


@pytest.mark.parametrize('text', ['x' * 5000, '雪😀\\\"' * 1300])
def test_long_read_fragments_include_context_in_their_budget(text):
    from _application.artefact.read import ArtefactLocation, ArtefactReadPayload
    from _application._response_budget import ContentRange
    from _application.projection import canonical_result_envelope
    from _application.results import Ok

    payload = ArtefactReadPayload('Wiki/Example.md', ArtefactLocation.ACTIVE,
        text, 'sha256:' + 'a' * 64, ContentRange(0, len(text), len(text), None))
    source = canonical_result_envelope(Ok('artefact.read', 4, payload))
    view = build_view(source)
    assert len(view.pages) > 1
    assert ''.join(fragment['text'] + fragment['line_ending']
                   for page in view.pages for fragment in page['result']['fragments']) == text
    for page in view.pages:
        assert body_fits(page['result'])
        for key in ('reference', 'location', 'revision', 'range'):
            assert page['result'][key] == source['result'][key]


def test_long_line_preserves_large_context_through_a_bounded_detail():
    context = {f'field-{i}': 'value' for i in range(300)}
    text = 'x' * 5000
    view = build_view(envelope({'content': text, **context}))
    assert ''.join(fragment['text'] for page in view.pages
                   for fragment in page['result']['fragments']) == text
    reference = view.pages[0]['result']['context']
    assert reference['detail'] in view.details
    assert all(body_fits(page['result']) for page in view.pages)
