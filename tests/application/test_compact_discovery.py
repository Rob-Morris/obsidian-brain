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


def test_discovery_distinguishes_capability_from_authorisation(tmp_path):
    catalogue = current_application_catalogue()
    allowed = {entry.command_id for entry in catalogue.entries} - {"artefact.delete"}
    initial = allowed - {"artefact.create"}
    app = CommandApplication(_context(tmp_path, allowed_commands=allowed, initial_commands=initial), catalogue)
    result = app.invoke(CommandListRequest(domain="artefact"))
    entries = {entry.command_id: entry for entry in result.result.entries}
    assert entries["artefact.delete"].access is CommandAccess.DENIED
    assert entries["artefact.delete"].static_disclosure
    assert entries["artefact.create"].access is CommandAccess.REQUIRED
    assert entries["artefact.read"].access is CommandAccess.AUTHORISED
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


def test_discovery_reads_one_batched_observation_without_consuming_consent(tmp_path, monkeypatch):
    from _application.requests import CommandDescribeRequest
    context = _context(tmp_path)
    app = CommandApplication(context, current_application_catalogue())
    reads = []
    original = context.access.command_access_many
    def read(ids):
        reads.append(ids)
        return original(ids)
    monkeypatch.setattr(context.access, "command_access_many", read)
    listed = app.invoke(CommandListRequest(view=CommandListView.DETAILED))
    assert listed.status == "ok"
    assert len(reads) == 1
    assert len(reads[0]) == len(current_application_catalogue().entries)
    described = app.invoke(CommandDescribeRequest("invocation.read"))
    assert described.result.access is CommandAccess.AUTHORISED
    assert len(reads) == 2
    assert not context.authorisation.receipts.intents


def test_above_maximum_discovery_uses_no_dynamic_provider_facts(tmp_path):
    from _application.requests import CommandDescribeRequest
    from _application.types import Availability, SnapshotFreshness
    from dataclasses import replace
    from _application.context import ProviderBindings
    class ForbiddenProviders:
        def get(self, provider_id):
            raise AssertionError(f"provider probed: {provider_id}")
    catalogue = current_application_catalogue()
    allowed = {"command.list", "command.describe", "session.start", "access.status", "access.prepare", "access.request", "access.reduce", "invocation.read"}
    context = replace(_context(tmp_path, allowed_commands=allowed), providers=ForbiddenProviders())
    app = CommandApplication(context, catalogue)
    for view in CommandListView:
        result = app.invoke(CommandListRequest(query="skill.add-git", view=view))
        row = result.result.entries[0]
        assert row.command_id == "skill.add-git"
        assert row.access is CommandAccess.DENIED
        assert row.availability is Availability.UNKNOWN
        assert row.static_disclosure
        assert "permission.set-profile" in row.permission_management
        if view is CommandListView.DETAILED:
            assert row.availability_freshness is SnapshotFreshness.UNKNOWN
            assert row.missing_optional_providers == ()
    result = app.invoke(CommandDescribeRequest("skill.add-git"))
    assert result.result.static_disclosure
    assert result.result.availability is Availability.UNKNOWN
    assert result.result.request_schema_json
