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
        text += payload["content"]
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
    view = build_view(envelope({"content": text}), 1, unit="lines")
    assert "".join(page["result"]["content"] for page in view.pages) == text
    assert view.source_lines == view.total_items == 4
    position = 0
    for page in view.pages:
        payload = page["result"]
        assert "fragments" not in payload
        assert payload["content_range"] == {"start": position,
            "end": position + len(payload["content"]), "total": len(text)}
        position += len(payload["content"])
    assert position == len(text)
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
        text += frame["result"]["content"]
        body_reference = frame["delivery"]["next_detail"]
    assert text == original["body"]
    assert all(body_fits(frame["result"]) for frame in view.details.values())


def test_small_structured_root_preserves_original_shape():
    original = {"path": "Example.md", "one": 1, "two": [False, None]}
    view = build_view(envelope(original))
    assert len(view.pages) == 1
    assert view.page()["result"] == original


def test_oversized_root_keeps_small_fields_without_json_dump():
    original = {"one": 1, "two": "x" * 10000}
    view = build_view(envelope(original))
    compact = view.page()["result"]
    assert set(compact) == set(original)
    assert compact["one"] == 1
    assert compact["two"]["detail_reference"] in view.details


@pytest.mark.parametrize("size", [True, False, 0, -1, 1.5, 101, "25"])
def test_invalid_record_sizes(size):
    with pytest.raises(ValueError):
        build_view(envelope({"items": []}), size)


def test_line_limits_and_declared_units():
    assert build_view(envelope({"content": "x"}), unit="lines").page_size == 100
    assert build_view(envelope({"content": "x"}), 500, unit="lines").page_size == 500
    with pytest.raises(ValueError):
        build_view(envelope({"content": "x"}), 501, unit="lines")
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
    assert not ({"total_pages", "total_items", "selected_pages"} & info.keys())


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
    view = build_view(envelope({"content": ("😀\\\"" * 5000)}), 1, unit="lines")
    assert all(actual_encoded(frame["result"]) <= ENCODED_BODY_BYTES for frame in view.pages)
    assert "".join(frame["result"]["content"] for frame in view.pages) == "😀\\\"" * 5000
    assert all("fragments" not in frame["result"] for frame in view.pages)
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
    source = build_view(envelope({"items": [{"id": 1, "body": "x" * 3600}]})).page()
    assert len(json.dumps(source).encode()) > 4000
    view = build_view(envelope({"envelope": source}))
    assert set(view.page()["result"]) == {"envelope"}
    assert all(body_fits(frame["result"]) for frame in (*view.pages, *view.details.values()))
    assert len(view.details) < 30


def test_near_budget_key_and_value_route_components_and_preserve_all_fields():
    original = {"k" * 2100: "v" * 1850, "availability_freshness": "current"}
    assert body_fits(original["k" * 2100])
    view = build_view(envelope(original))
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
        assert ''.join(page['result']['content'] for page in view.pages) == text
        assert all(original(page['result']) for page in view.pages)
    # An eightfold longer line needs proportional bounded fragment probes,
    # rather than repeatedly encoding its entire remaining suffix.
    assert work[1] < 10 * work[0]


@pytest.mark.parametrize('text', ['x' * 5000, '雪😀\\\"' * 1300])
def test_long_read_fragments_include_context_in_their_budget(text):
    source = {**envelope({"reference": "Wiki/Example.md", "location": "active",
                          "content": text, "revision": "sha256:" + "a" * 64}),
              "command": "artefact.read"}
    view = build_view(source)
    assert len(view.pages) > 1
    assert ''.join(page['result']['content'] for page in view.pages) == text
    for page in view.pages:
        assert body_fits(page['result'])
        for key in ('reference', 'location', 'revision'):
            assert page['result'][key] == source['result'][key]


def test_long_line_preserves_large_context_through_a_bounded_detail():
    context = {f'field-{i}': 'value' for i in range(300)}
    text = 'x' * 5000
    view = build_view(envelope({'content': text, **context}))
    assert ''.join(page['result']['content'] for page in view.pages) == text
    reference = view.pages[0]['result']['context']
    assert reference['detail'] in view.details
    assert all(body_fits(page['result']) for page in view.pages)


@pytest.mark.parametrize("size", [1, 2, 100, 500])
def test_text_pages_count_logical_lines_without_duplicate_text(size):
    text = "one\r\ntwo\nthree\u2028last"
    view = build_view(envelope({"content": text}), size, unit="lines")
    assert "".join(page["result"]["content"] for page in view.pages) == text
    assert len(view.pages) == (4 + size - 1) // size
    assert all(len(page["result"]["content"].splitlines()) <= size for page in view.pages)
    assert all(set(page["result"]) == {"content", "content_range"} for page in view.pages)


def test_small_text_uses_one_representation_and_empty_range_is_stable():
    text = "ordinary line\n" * 200
    view = build_view(envelope({"content": text}), 500, unit="lines")
    assert len(view.pages) == 1
    assert view.page()["result"] == {"content": text,
        "content_range": {"start": 0, "end": len(text), "total": len(text)}}
    empty = build_view(envelope({"content": ""})).page()
    assert empty["result"] == {"content": "", "content_range": {"start": 0, "end": 0, "total": 0}}
    assert empty["delivery"]["total_pages"] == 0


def test_page_line_bounds_match_delivered_text_at_exact_boundaries():
    view = build_view(envelope({"content": "one\ntwo\nthree"}), 1, unit="lines")
    assert [(page["delivery"]["first_item"], page["delivery"]["last_item"])
            for page in view.pages] == [(1, 1), (2, 2), (3, 3)]


def test_previous_layout_is_rejected_instead_of_delivering_fragments():
    view = build_view(envelope({"content": "read"}))
    old = view.metadata()
    old.pop("layout_version")
    with pytest.raises(ValueError, match="layout changed"):
        select_page(view.pages[0], old)


def test_compact_default_metadata_keeps_counts_without_selection_boilerplate():
    info = build_view(envelope({"content": "text"})).page()["delivery"]
    assert info["total_items"] == 4
    assert info["total_pages"] == info["page"] == 1
    assert info["status"] == "complete" and info["continuation"] is None
    assert not ({"selection_id", "selected_pages", "start_page", "end_page",
                 "pages_before_selection", "pages_after_selection", "source_revision",
                 "change_notice", "fragments", "requested_page_size"} & info.keys())
    assert all(value is not None or key == "continuation" for key, value in info.items())


def test_retained_text_search_crosses_delivery_pages_without_public_fragments(tmp_path):
    from types import SimpleNamespace
    from _command_interface.result_delivery import ResultDeliverySession
    from _command_interface.result_store import FileResultStore
    from _application.result.read import ResultReadRequest
    from _application.result.search import ResultSearchRequest

    root = tmp_path / "Brain"
    root.mkdir()
    store = FileResultStore(root, "brain", "principal")
    session = ResultDeliverySession(store, store.issue())
    text = "header\n" + "x" * 15000
    first = build_view(envelope({"content": text}))
    boundary = first.pages[0]["result"]["content_range"]["end"]
    text = text[:boundary - 2] + "NEEDLE" + text[boundary + 4:]
    source = envelope({"content": text})
    view = build_view(source)
    key = session._view_key(view.unit, view.page_size)
    authority = {"command_id": "test.read", "stamp": None, "default_view": key}
    ticket = store.admit(session.owner, "test.read", "invocation")
    result_id = store.publish(session.owner, ticket, source, authority)
    session._save(result_id, key, view, None)
    service = SimpleNamespace(check_result_delivery=lambda *args: None)
    context = SimpleNamespace(authorisation=SimpleNamespace(service=service))
    matches = []
    page, offset = 1, 0
    while page is not None:
        found = session.search(context, ResultSearchRequest(result_id, "NEEDLE",
            start_page=page, start_offset=offset, scan_pages=1))
        matches.extend(found["matches"])
        page, offset = found["next_page"], found["next_offset"]
    assert [(match["line"], match["column"]) for match in matches] == [(2, boundary - 9)]
    delivered = session.read(context, ResultReadRequest(result_id, page=2))
    assert "_text_column" not in delivered
    assert "fragments" not in delivered["result"]


def test_retained_previous_layout_is_rejected_without_replaying_source(tmp_path):
    from types import SimpleNamespace
    from _command_interface.result_delivery import ResultDeliverySession
    from _command_interface.result_store import FileResultStore
    from _application.result.read import ResultReadRequest

    root = tmp_path / "Brain"
    root.mkdir()
    store = FileResultStore(root, "brain", "principal")
    session = ResultDeliverySession(store, store.issue())
    source = envelope({"content": "text"})
    view = build_view(source)
    authority = {"command_id": "test.read", "stamp": None, "default_view": "old"}
    ticket = store.admit(session.owner, "test.read", "invocation")
    result_id = store.publish(session.owner, ticket, source, authority)
    old = view.metadata()
    old.pop("layout_version")
    store.save_view(session.owner, result_id, "old", list(view.pages), old)
    service = SimpleNamespace(check_result_delivery=lambda *args: None)
    context = SimpleNamespace(authorisation=SimpleNamespace(service=service))
    with pytest.raises(ValueError, match="Do not rerun a mutation"):
        session.read(context, ResultReadRequest(result_id))


def test_explicit_field_page_size_limits_units_without_changing_default_root():
    data = {f"field{i}": i for i in range(6)}
    default = build_view(envelope(data))
    assert len(default.pages) == 1 and default.page()["result"] == data
    selected = build_view(envelope(data), page_size=2)
    assert len(selected.pages) == 3
    assert all(len(page["result"]["fields"]) <= 2 for page in selected.pages)
    rebuilt = {entry["field"]: entry["value"] for page in selected.pages
               for entry in page["result"]["fields"]}
    assert rebuilt == data
    assert all(page["delivery"]["total_pages"] == 3 for page in selected.pages)


def test_default_compact_root_and_explicit_default_field_size_have_distinct_identities():
    data = {f"field{i}": i for i in range(40)}
    compact = build_view(envelope(data))
    explicit = build_view(envelope(data), page_size=25)
    assert len(compact.pages) == 1 and len(explicit.pages) == 2
    assert compact.view_id != explicit.view_id


@pytest.mark.parametrize('size', [None, 1])
def test_abandoned_compact_root_does_not_retain_unreferenced_details(size):
    data = {f'field{i}': 'x' * 600 for i in range(35)}
    view = build_view(envelope(data), page_size=size)
    assert len(view.pages) > 1
    assert not view.details and not view.metadata()['has_details']
    recovered = {item['field']: item['value'] for page in view.pages for item in page['result']['fields']}
    assert recovered == data


def test_default_text_bytes_fit_many_lines_on_one_page():
    text = "x\n" * 200
    view = build_view(envelope({"content": text}))
    assert view.unit == "bytes" and view.page_size == 4000
    assert len(view.pages) == 1
    assert view.page()["result"]["content"] == text
    info = view.page()["delivery"]
    assert (info["first_item"], info["last_item"], info["total_items"]) == (1, 400, 400)
    lines = build_view(envelope({"content": text}), unit="lines")
    assert lines.page_size == 100 and len(lines.pages) == 2
    assert lines.total_items == 200


@pytest.mark.parametrize("text,size", [("abcde", 2), ("雪😀éabc", 4), ("😀😀😀", 5)])
def test_explicit_byte_sizes_preserve_unicode_and_report_byte_bounds(text, size):
    view = build_view(envelope({"content": text}), size)
    assert view.unit == "bytes" and view.page_size == size
    assert view.total_items == len(text.encode("utf-8"))
    char_position = byte_position = 0
    for page in view.pages:
        payload, info = page["result"], page["delivery"]
        delivered = payload["content"]
        byte_count = len(delivered.encode("utf-8"))
        assert 0 < byte_count <= size
        assert (info["first_item"], info["last_item"]) == (byte_position + 1, byte_position + byte_count)
        assert payload["content_range"] == {"start": char_position,
            "end": char_position + len(delivered), "total": len(text)}
        assert body_fits(payload)
        char_position += len(delivered)
        byte_position += byte_count
    assert "".join(page["result"]["content"] for page in view.pages) == text
    assert byte_position == view.total_items


@pytest.mark.parametrize("text,size,minimum", [("😀", 3, 4), ("a雪", 2, 3)])
def test_byte_size_too_small_for_next_character_is_actionable(text, size, minimum):
    with pytest.raises(ValueError, match=f"at least {minimum} UTF-8 bytes.*increase page_size to 4000.*default"):
        build_view(envelope({"content": text}), size)


def test_byte_mode_obeys_final_body_budget_without_exceeding_requested_size():
    text = '😀\\"' * 2000
    view = build_view(envelope({"content": text}), 4000)
    assert len(view.pages) > 1
    assert all(body_fits(page["result"]) for page in view.pages)
    assert all(len(page["result"]["content"].encode("utf-8")) <= 4000 for page in view.pages)
    assert len(view.pages[0]["result"]["content"].encode("utf-8")) < 4000
    assert "".join(page["result"]["content"] for page in view.pages) == text


@pytest.mark.parametrize("size", [True, 0, -1, 1.5, 4001, "100"])
def test_invalid_byte_sizes(size):
    with pytest.raises(ValueError, match="page_size"):
        build_view(envelope({"content": "text"}), size)


@pytest.mark.parametrize("payload,unit", [({"content": "x"}, "records"),
    ({"content": "x"}, "fields"), ({"items": [1]}, "bytes"),
    ({"items": [1]}, "lines"), ({"key": "value"}, "bytes"),
    ({"key": "value"}, "lines")])
def test_incompatible_explicit_unit_is_rejected(payload, unit):
    with pytest.raises(ValueError, match="unit must"):
        build_view(envelope(payload), unit=unit)


def test_text_view_identity_binds_mode_and_size():
    source = envelope({"content": "a\nb\nc"})
    default = build_view(source)
    assert default.view_id == build_view(source, 4000, "bytes").view_id
    assert len({build_view(source, size, unit).view_id
        for size, unit in [(2, "bytes"), (3, "bytes"), (2, "lines")]}) == 3
    assert all(page["delivery"]["unit"] == "bytes" for page in default.pages)


def test_line_mode_long_line_keeps_logical_line_bounds_under_byte_cap():
    text = "雪" * 3000 + "\nend\n"
    view = build_view(envelope({"content": text}), 1, "lines")
    assert len(view.pages) > 2
    assert "".join(page["result"]["content"] for page in view.pages) == text
    assert view.total_items == 2
    assert all(body_fits(page["result"]) for page in view.pages)
    assert all(page["delivery"]["first_item"] == page["delivery"]["last_item"] for page in view.pages)
    assert view.pages[0]["delivery"]["first_item"] == 1
    assert view.pages[-1]["delivery"]["last_item"] == 2
