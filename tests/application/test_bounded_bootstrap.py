"""Bootstrap produces complete canonical text for bounded generic delivery."""

from _application.session.start import SessionStartRequest
from _application.vault.read_file import VaultReadFileRequest
from _application.types import DependencyTier
from command_application import application_for
from test_session_start_owner import _PublishedSessionMirror, _complete_bootstrap, _mark_ready


def test_large_bootstrap_returns_complete_mirror_and_refreshes_changed_source(command_vault_clone, record_property):
    root = command_vault_clone.vault_root
    path = root / "_Config/User/preferences-always.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('# Mandatory preferences\n' + '文"\\🧠\n' * 9000 + '\nFINAL MANDATORY RULE\n')
    _mark_ready(root)
    mirror = _PublishedSessionMirror(root)
    app = application_for(root, dependency_tier=DependencyTier.MANAGED, session_mirror=mirror)
    result = app.invoke(SessionStartRequest())
    assert result.status == "ok", result
    assert not hasattr(result.result, "bootstrap_complete")
    assert result.result.content == (root / ".brain/local/session.md").read_text()
    assert "FINAL MANDATORY RULE" in result.result.content
    assert not hasattr(result.result, "range")
    path.write_text("New mandatory preference.")
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
