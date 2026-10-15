"""Relevance is explicit metadata with soft history, never implicit ownership."""

from dataclasses import replace

import pytest

import compile_router
from _application._mutation_support import FrontmatterField, InlineContent
from _application.artefact.archive import ArtefactArchiveRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
from _application.registry import current_application_catalogue
from _application.vault.check import VaultCheckRequest
from _application.workspace.update_metadata import WorkspaceUpdateMetadataRequest
from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
from _application.workspace_context import WorkspaceSelector
from _common import document_revision_at, parse_frontmatter, serialize_frontmatter
from _lifecycle.derived_cache_state import require_fresh_compiled_router
from test_workspace_mutation_context import scoped, create_request
from test_workspace_skill_preparation import MatchingAdmission


def _refresh(root):
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))


def _historical_target(app, *, archive):
    assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/beta", clear_parent=True)).status == "ok"
    closed = app.invoke(ArtefactSetStatusRequest("workspace/beta", "completed"))
    assert closed.status == "ok", closed
    if not archive:
        return closed.result.path
    archived = app.invoke(ArtefactArchiveRequest("workspace/beta", workspace_context=WorkspaceSelector("global")))
    assert archived.status == "ok", archived
    return archived.result.new_path


@pytest.mark.parametrize("tag", ["workspace/missing", "workspace/123", "workspace/../escape"])
def test_new_workspace_associations_refuse_unknown_or_malformed_without_effects(scoped, tag):
    root, _folder, app, _parents = scoped
    created = app.invoke(create_request(frontmatter=(FrontmatterField("tags", (tag,)),)))
    assert created.status == "error" and created.effects == "none"
    assert "workspace" in created.error.message.lower()
    assert not (root / "Designs/project~local/Subject.md").exists()


@pytest.mark.parametrize("archive", [False, True])
def test_new_association_accepts_historical_identity_without_selecting_its_defaults(scoped, archive):
    root, _folder, app, _parents = scoped
    _historical_target(app, archive=archive)
    result = app.invoke(create_request(frontmatter=(FrontmatterField("tags", ("workspace/beta",)),)))
    assert result.status == "ok", result.error.message if result.status == "error" else result
    fields, _body = parse_frontmatter((root / result.result.path).read_text())
    assert fields["workspace"] == "workspace/alpha"
    assert fields["parent"] == "project/local"
    assert "beta" not in fields["tags"]
    assert set(result.result.mutation_context.relevant_workspaces) == {"workspace/alpha", "workspace/beta"}


@pytest.mark.parametrize("tag,code", [("workspace/missing", "workspace_relevance_missing"),
                                     ("workspace/123", "workspace_relevance_malformed")])
def test_historical_bad_association_is_informational_and_does_not_block_body_edit(scoped, tag, code):
    root, _folder, app, _parents = scoped
    result = app.invoke(create_request())
    assert result.status == "ok", result
    path = root / result.result.path
    fields, body = parse_frontmatter(path.read_text())
    fields["tags"].append(tag)
    path.write_text(serialize_frontmatter(fields, body=body))
    _refresh(root)
    updated = app.invoke(DocumentWriteBodyRequest(DocumentLocator(DocumentResource.ARTEFACT, result.result.path),
        document_revision_at(path), DocumentWriteBodyOperation.APPEND, InlineContent("\nStill editable.\n")))
    assert updated.status == "ok", updated.error.message if updated.status == "error" else updated
    assert tag in parse_frontmatter(path.read_text())[0]["tags"]
    checked = app.invoke(VaultCheckRequest(check="workspace_contract", actionable=True))
    assert checked.status == "ok", checked
    findings = [finding for finding in checked.result.findings if finding.file == result.result.path]
    assert any(finding.code == code and finding.severity == "info" for finding in findings)
    assert not any(finding.severity == "error" for finding in findings)


def test_explicit_tag_edit_rejects_new_missing_association_but_preserves_original_bytes(scoped):
    root, _folder, app, _parents = scoped
    created = app.invoke(create_request())
    path = root / created.result.path
    before = path.read_bytes()
    fields, _body = parse_frontmatter(before.decode())
    update = DocumentUpdateFrontmatterRequest(DocumentLocator(DocumentResource.ARTEFACT, created.result.path),
        document_revision_at(path), (FrontmatterField("tags", (*fields["tags"], "workspace/missing")),))
    result = app.invoke(update)
    assert result.status == "error" and result.effects == "none"
    assert path.read_bytes() == before


@pytest.mark.parametrize("archive", [False, True])
def test_association_identity_revision_is_admission_bound(scoped, archive):
    root, _folder, app, _parents = scoped
    target = _historical_target(app, archive=archive)
    request = create_request(frontmatter=(FrontmatterField("tags", ("workspace/beta",)),))
    entry = current_application_catalogue().resolve(request)
    binding = entry.preparation.prepare(app._context, request)
    assert any(item.kind == "workspace-association" and item.identity == "workspace/beta"
               for item in binding.observations)
    path = root / target
    changed = path.read_text() + "\nHistorical identity revised.\n"
    path.write_text(changed)
    admission = MatchingAdmission(binding)
    result = entry.executor(replace(app._context, admission=admission), request)
    assert result.status == "error" and result.effects == "none"
    assert admission.calls == 0
    assert path.read_text() == changed


@pytest.mark.parametrize("local", [False, True])
def test_new_policy_association_requires_existing_identity(scoped, local):
    root, folder, app, _parents = scoped
    source = folder / ".brain/local/workspace.yaml" if local else root / require_fresh_compiled_router(root)["artefact_index"]["workspace/alpha"]["path"]
    before = source.read_bytes()
    request = (WorkspaceUpdateMetadataRequest(tags=("workspace/missing",)) if local else
               WorkspaceUpdatePolicyRequest("workspace/alpha", default_tags=("workspace/missing",)))
    result = app.invoke(request)
    assert result.status == "error" and result.effects == "none"
    assert source.read_bytes() == before


def test_inherited_stale_policy_association_does_not_block_unrelated_creation(scoped):
    root, _folder, app, _parents = scoped
    hub = root / require_fresh_compiled_router(root)["artefact_index"]["workspace/alpha"]["path"]
    fields, body = parse_frontmatter(hub.read_text())
    fields["default_tags"].append("workspace/missing")
    hub.write_text(serialize_frontmatter(fields, body=body))
    _refresh(root)
    created = app.invoke(create_request())
    assert created.status == "ok", created.error.message if created.status == "error" else created
    fields, _body = parse_frontmatter((root / created.result.path).read_text())
    assert fields["workspace"] == "workspace/alpha" and "workspace/missing" in fields["tags"]
