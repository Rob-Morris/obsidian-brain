"""Bound discovery by real encoded bytes without changing command authority."""

from dataclasses import replace
import json

import pytest

from _application.application import CommandApplication
from _application.foundation import build_application_catalogue
from _application.projection import canonical_result_envelope
from _application.registry import current_application_catalogue
from _application.requests import CommandAccess, CommandListRequest, CommandListView
from test_foundation_commands import _context


@pytest.mark.parametrize("view", list(CommandListView))
def test_discovery_pages_fit_encoded_budget_without_losing_entries(tmp_path, view):
    additional = tuple(
        replace(entry, summary=entry.summary + ' 文"\\' * 100 + ".")
        for entry in current_application_catalogue().entries
        if entry.command_id not in {"command.list", "command.describe", "invocation.read"}
    )
    catalogue = build_application_catalogue(additional)
    app = CommandApplication(_context(tmp_path), catalogue)
    cursor = None
    found = []
    while True:
        result = app.invoke(CommandListRequest(view=view, page_size=500, cursor=cursor))
        envelope = canonical_result_envelope(result)
        assert result.status == "ok"
        assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()) <= 16000
        assert result.result.entries
        found.extend(result.result.command_ids)
        cursor = result.result.next_cursor
        if cursor is None:
            break
    assert found == [entry.command_id for entry in catalogue.entries]


def test_discovery_distinguishes_capability_from_active_grant(tmp_path):
    class Authority:
        def ceiling_allows(self, command_id):
            return command_id != "artefact.delete"

        def observe(self):
            return self

        def allows(self, *, command_id, **_kwargs):
            return command_id != "artefact.create"

        def consume(self, command_id):
            assert command_id == "command.list"
            return True

    app = CommandApplication(replace(_context(tmp_path), authority=Authority()),
                             current_application_catalogue())
    result = app.invoke(CommandListRequest(domain="artefact"))
    entries = {entry.command_id: entry for entry in result.result.entries}
    assert "artefact.delete" not in entries
    assert entries["artefact.create"].access is CommandAccess.INACTIVE
    assert entries["artefact.read"].access is CommandAccess.ACTIVE
    assert entries["artefact.create"].availability == entries["artefact.read"].availability


@pytest.mark.parametrize("query, expected", [
    ("heading section", "document.structured-edit"),
    ("search index", "retrieval.refresh-lexical"),
    ("permissions", "command.describe"),
    ("interrupted mutation", "invocation.read"),
])
def test_task_words_find_relevant_commands(tmp_path, query, expected):
    app = CommandApplication(_context(tmp_path), current_application_catalogue())
    result = app.invoke(CommandListRequest(query=query))
    assert expected in result.result.command_ids


def test_generated_discovery_schemas_require_access():
    from _application.projection import result_payload_schema
    from _application.requests import CommandDescribeRequest

    schema = result_payload_schema(CommandListRequest)
    entries = schema["properties"]["entries"]["items"]["anyOf"]
    assert len(entries) == 2
    assert all("access" in entry["required"] for entry in entries)
    assert "access" in result_payload_schema(CommandDescribeRequest)["required"]


def test_discovery_reads_one_access_snapshot_without_consuming_listed_leases(tmp_path, monkeypatch):
    from _application.requests import CommandDescribeRequest
    from _command_interface.context import ProfileAuthority
    from test_access_grant_owners import _controller

    root, controller, clock = _controller(tmp_path)
    controller.request(("invocation.read",), duration_seconds=60, use_count=2)
    authority = ProfileAuthority("contributor", controller.ceiling_commands, controller)
    app = CommandApplication(replace(_context(root), authority=authority),
                             build_application_catalogue())
    reads = []
    consumes = []
    original_read = controller._read_state
    original_consume = controller.consume

    def read():
        reads.append(True)
        return original_read()

    def consume(command_id):
        consumes.append(command_id)
        return original_consume(command_id)

    monkeypatch.setattr(controller, "_read_state", read)
    monkeypatch.setattr(controller, "consume", consume)
    listed = app.invoke(CommandListRequest(view=CommandListView.DETAILED))
    assert len(reads) == 1
    assert listed.result.entries[-1].access is CommandAccess.ACTIVE
    described = app.invoke(CommandDescribeRequest("invocation.read"))
    assert len(reads) == 2
    assert described.result.access is CommandAccess.ACTIVE
    assert consumes == ["command.list", "command.describe"]
    assert controller.status().leases[0].remaining_uses == 2

    frozen = authority.observe()
    clock.advance(61)
    parameters = dict(command_id="invocation.read", required=listed.result.entries[-1].authority,
                      effect=listed.result.entries[-1].effect_class)
    assert frozen.allows(**parameters)
    assert not authority.allows(**parameters)
