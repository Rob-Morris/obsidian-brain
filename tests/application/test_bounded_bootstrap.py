"""Bootstrap stays bounded while preserving every mandatory instruction."""

import json
from dataclasses import replace

from _application.session.start import SessionStartRequest
from _application.vault.read_file import VaultReadFileRequest
from _application.projection import canonical_result_envelope
from _application.types import DependencyTier
from _application.results import ErrorCode
from command_application import application_for
from test_session_start_owner import _PublishedSessionMirror, _complete_bootstrap, _mark_ready


def test_large_bootstrap_matches_complete_mirror_and_detects_changed_source(command_vault_clone, record_property):
    root = command_vault_clone.vault_root
    path = root / "_Config/User/preferences-always.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('# Mandatory preferences\n' + '文"\\🧠\n' * 9000 + '\nFINAL MANDATORY RULE\n')
    _mark_ready(root)
    mirror = _PublishedSessionMirror(root)
    app = application_for(root, dependency_tier=DependencyTier.MANAGED, session_mirror=mirror)
    pages = []
    cursor = None
    first_cursor = None
    while True:
        result = app.invoke(SessionStartRequest(cursor))
        assert result.status == "ok", result
        envelope = canonical_result_envelope(result)
        assert len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()) < 16000
        pages.append(result.result.content)
        cursor = result.result.range.next_cursor
        first_cursor = first_cursor or cursor
        assert result.result.bootstrap_complete == (cursor is None)
        if cursor is None:
            break
    assert len(pages) > 1
    assert "".join(pages) == (root / ".brain/local/session.md").read_text()
    assert "FINAL MANDATORY RULE" in "".join(pages)
    path.write_text("New mandatory preference.")
    conflict = app.invoke(SessionStartRequest(first_cursor))
    assert conflict.error.code is ErrorCode.CONFLICT
    restarted, rendered = _complete_bootstrap(app, root, mirror, record_property)
    assert restarted["preferences"] == "New mandatory preference."
    assert "New mandatory preference." in rendered
    assert "FINAL MANDATORY RULE" not in rendered


def test_lean_bootstrap_retains_retrieval_routes_and_all_core_links_are_readable(command_vault_clone, record_property):
    root = command_vault_clone.vault_root
    _mark_ready(root)
    mirror = _PublishedSessionMirror(root)
    app = application_for(root, dependency_tier=DependencyTier.MANAGED, session_mirror=mirror)
    bootstrap, rendered = _complete_bootstrap(app, root, mirror, record_property)
    assert bootstrap["artefact_type_count"] > 0
    assert "resource.list" in bootstrap["resource_discovery"]["artefact_types"]
    assert "resource.read" in bootstrap["resource_discovery"]["artefact_types"]
    assert "vault.read-file" in bootstrap["resource_discovery"]["core_documents"]
    assert bootstrap["core_docs"]
    for section in bootstrap["core_docs"]:
        for doc in section["docs"]:
            assert doc["path"] in rendered
            request = VaultReadFileRequest(doc["path"])
            page = app.invoke(request)
            assert page.status == "ok", (doc["path"], page)
            while page.result.range.next_cursor:
                page = app.invoke(replace(request, cursor=page.result.range.next_cursor))
                assert page.status == "ok", (doc["path"], page)
