"""Workspace identity and policy behavior across the two setup boundaries."""

from dataclasses import replace
import json

import pytest
import vault_registry
from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
from _common._workspace import (membership, resolve_workspace_binding, validate_ownership,
                               workspace_policy, workspace_reference)
from _application.context import Capability
from _application.types import Availability, DependencyTier
from _application.workspace.setup import WorkspaceSetupRequest, execute as setup, prepare_setup
from _application.workspace.ensure_registration import WorkspaceEnsureRegistrationRequest
from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
from _application.workspace.update_metadata import WorkspaceUpdateMetadataRequest
from command_application import application_for
from compile_router import _hash_index_payload
from test_workspace_skill_preparation import MatchingAdmission


class CallerFilesystem:
    provider_id = "caller_filesystem"


def application(root, workspace=None):
    return application_for(root, workspace_dir=workspace, dependency_tier=DependencyTier.PORTABLE,
        providers=(CallerFilesystem(),), capabilities=(Capability("caller_filesystem", Availability.AVAILABLE),))


def router():
    return {"artefacts": [{"frontmatter_type": "living/workspace", "frontmatter": {"terminal_statuses": ["completed"]}},
                          {"frontmatter_type": "living/project", "frontmatter": {"terminal_statuses": ["completed"]}}],
            "artefact_index": {"workspace/demo": {"type": "living/workspace", "status": "active", "path": "Workspaces/Demo.md"},
                               "project/owner": {"type": "living/project", "status": "active", "workspace": "workspace/demo"}}}


def test_canonical_membership_never_uses_relationship_tags():
    assert workspace_reference("demo", bare=True) == "workspace/demo"
    assert workspace_reference("workspace~demo") == "workspace/demo"
    with pytest.raises(ValueError):
        workspace_reference("project/demo")
    assert membership("project/demo", {"type": "living/project", "tags": ["workspace/demo"]}) is None
    assert membership("workspace/demo", {"type": "living/workspace"}) == "workspace/demo"
    with pytest.raises(ValueError, match="self-scoped"):
        membership("workspace/demo", {"type": "living/workspace", "workspace": "workspace/other"})


@pytest.mark.parametrize("child,parent,valid", [(None, None, True), ("workspace/demo", "workspace/demo", True),
                                               (None, "workspace/demo", False), ("workspace/demo", None, False),
                                               ("workspace/demo", "workspace/other", False)])
def test_ownership_edges_have_one_membership(child, parent, valid):
    entry = {"type": "living/project", "workspace": child, "parent": "project/owner"}
    index = {"project/owner": {"type": "living/project", "workspace": parent}}
    if valid:
        validate_ownership("project/child", entry, index)
    else:
        with pytest.raises(ValueError, match="share one workspace"):
            validate_ownership("project/child", entry, index)


def test_shared_and_local_policy_use_same_parent_rule():
    view = router()
    shared = workspace_policy(view, "workspace/demo", {"default_parent": "project~owner", "default_tags": ["a", "a", "b"]})
    local = workspace_policy(view, "workspace/demo", {"parent": "project/owner", "tags": ["a", "b"]}, local=True)
    assert shared == local
    view["artefact_index"]["project/owner"]["status"] = "completed"
    with pytest.raises(ValueError, match="terminal"):
        workspace_policy(view, "workspace/demo", {"default_parent": "project/owner"})
    with pytest.raises(ValueError, match="list"):
        workspace_policy(view, "workspace/demo", {"default_tags": "bad"})


@pytest.mark.parametrize("scope", [None, "workspace/other"])
def test_default_parent_rejects_unscoped_and_other_workspace_ownership(scope):
    view = router()
    view["artefact_index"]["project/owner"]["workspace"] = scope
    with pytest.raises(ValueError, match="must belong"):
        workspace_policy(view, "workspace/demo", {"default_parent": "project/owner"})


def test_binding_states_are_exact_and_terminal_identity_survives():
    view = router()
    assert resolve_workspace_binding(view, None)[0] == "unconfigured"
    assert resolve_workspace_binding(view, {"slug": "demo"})[0] == "configured_invalid"
    assert resolve_workspace_binding(view, {"links": {"workspace": "missing"}})[:2] == ("configured_invalid", "workspace/missing")
    manifest = {"brain": "demo-brain", "slug": "repo", "links": {"workspace": "demo"}}
    assert resolve_workspace_binding(view, manifest)[:2] == ("valid", "workspace/demo")
    view["artefact_index"]["workspace/demo"]["status"] = "completed"
    assert resolve_workspace_binding(view, manifest)[0] == "terminal_inactive"


@pytest.mark.parametrize("field,value", [("workspace", "workspace/demo"), ("status", "completed"),
                                        ("default_parent", "project/owner"), ("default_tags", ["tag"])])
def test_index_source_fingerprint_tracks_membership_lifecycle_and_policy(field, value):
    fields = {"type": "living/workspace", "key": "demo"}
    assert _hash_index_payload(fields) != _hash_index_payload({**fields, field: value})
    assert _hash_index_payload({**fields, "body": "irrelevant"}) == _hash_index_payload(fields)


def test_setup_creates_then_attaches_and_keeps_explicit_link(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    vault_registry.register(root, "demo-brain")
    save_workspace_manifest_data(workspace, {"brain": "demo-brain", "slug": "repo", "links": {"workspace": "canonical"}, "defaults": {"tags": ["local"]}})
    app = application(root, workspace)
    result = app.invoke(WorkspaceSetupRequest())
    assert result.status == "ok", result
    assert result.result.registration.reference == "workspace/canonical"
    assert result.result.registration.status == "created"
    assert result.result.binding.slug == "repo"
    assert read_workspace_manifest(workspace)["defaults"] == {"tags": ["local"]}
    again = app.invoke(WorkspaceSetupRequest())
    assert again.status == "ok", again
    assert again.result.registration.status == "attached"
    assert again.committed_effects == ()
    compiled = json.loads((root / ".brain/local/compiled-router.json").read_text())
    assert "workspace/canonical" in compiled["artefact_index"]


def test_setup_one_admission_with_separate_locks_and_known_second_boundary_partial(command_vault_clone, tmp_path, monkeypatch):
    from contextlib import contextmanager
    import _common
    from _bootstrap import workspace_binding

    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    vault_registry.register(root, "demo-brain")
    context = application(root, workspace)._context
    request = WorkspaceSetupRequest()
    admission = MatchingAdmission(prepare_setup(context, request))
    held = []
    original_lock = _common.vault_mutation_lock
    @contextmanager
    def distinct_locks(path, *args, **kwargs):
        assert not held or held[-1] == path
        held.append(path)
        try:
            with original_lock(path, *args, **kwargs):
                yield
        finally:
            held.pop()
    monkeypatch.setattr(_common, "vault_mutation_lock", distinct_locks)
    original_save = workspace_binding.save_workspace_manifest_data
    def fail(*args, **kwargs):
        raise OSError("local disk refused write")
    monkeypatch.setattr(workspace_binding, "save_workspace_manifest_data", fail)
    result = setup(replace(context, admission=admission), request)
    assert result.status == "partial", result
    assert admission.calls == 1
    assert [effect.kind for effect in result.committed_effects] == ["workspace.registered", "workspace.path-registered"]
    monkeypatch.setattr(workspace_binding, "save_workspace_manifest_data", original_save)
    retry = application(root, workspace).invoke(request)
    assert retry.status == "ok", retry
    assert retry.result.registration.status == "attached"


def test_policy_update_validates_default_parent_and_local_clear(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    vault_registry.register(root, "demo-brain")
    app = application(root, workspace)
    created = app.invoke(WorkspaceEnsureRegistrationRequest("demo"))
    assert created.status == "ok", created
    save_workspace_manifest_data(workspace, {"brain": "demo-brain", "slug": "repo", "links": {"workspace": "demo"}, "defaults": {"tags": ["local"]}})
    # A hub is a living, self-scoped eligible default parent.
    policy = app.invoke(WorkspaceUpdatePolicyRequest("workspace/demo", "workspace/demo", default_tags=("shared", "shared")))
    assert policy.status == "ok", policy
    assert policy.result.default_tags == ("shared",)
    local = app.invoke(WorkspaceUpdateMetadataRequest(parent="workspace/demo"))
    assert local.status == "ok", local
    assert read_workspace_manifest(workspace)["defaults"] == {"tags": ["local"], "parent": "workspace/demo"}
    cleared = app.invoke(WorkspaceUpdateMetadataRequest(clear_parent=True))
    assert cleared.status == "ok", cleared
    assert read_workspace_manifest(workspace)["defaults"] == {"tags": ["local"]}
    invalid = app.invoke(WorkspaceUpdatePolicyRequest("workspace/demo", "project/missing"))
    assert invalid.status == "error"
    assert invalid.effects == "none"


def test_setup_preparation_is_read_only_without_machine_registration(command_vault_clone, tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    path = vault_registry.registry_path()
    context = application(command_vault_clone.vault_root, workspace)._context
    from pathlib import Path
    before = Path(path).read_bytes() if Path(path).exists() else None
    with pytest.raises(ValueError, match="machine registration"):
        prepare_setup(context, WorkspaceSetupRequest())
    assert (Path(path).read_bytes() if Path(path).exists() else None) == before


def test_shared_policy_preparation_rejects_live_hub_revision_drift(command_vault_clone):
    from _application.workspace.update_policy import prepare, execute

    root = command_vault_clone.vault_root
    app = application(root)
    created = app.invoke(WorkspaceEnsureRegistrationRequest("demo"))
    assert created.status == "ok", created
    request = WorkspaceUpdatePolicyRequest("workspace/demo", default_tags=("shared",))
    context = app._context
    admission = MatchingAdmission(prepare(context, request))
    path = root / created.result.path
    changed = path.read_text() + "\nConcurrent body edit\n"
    path.write_text(changed)
    result = execute(replace(context, admission=admission), request)
    assert result.status == "error", result
    assert admission.calls == 0
    assert path.read_text() == changed


@pytest.mark.parametrize("terminal", [False, True])
def test_generic_writers_cannot_bypass_workspace_policy(command_vault_clone, terminal):
    from _application.artefact.create import ArtefactCreateRequest
    from _application.artefact.set_status import ArtefactSetStatusRequest
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
    from _application._mutation_support import FrontmatterField
    from _common import document_revision_at
    from _lifecycle.derived_cache_state import inspect_router_cache, require_fresh_compiled_router

    root = command_vault_clone.vault_root
    app = application(root)
    hub = app.invoke(WorkspaceEnsureRegistrationRequest("demo"))
    other = app.invoke(WorkspaceEnsureRegistrationRequest("other"))
    assert hub.status == other.status == "ok"
    if terminal:
        closed = app.invoke(ArtefactSetStatusRequest("workspace/other", "completed"))
        assert closed.status == "ok", closed
    invalid = app.invoke(WorkspaceUpdatePolicyRequest("workspace/demo", "workspace/other"))
    assert invalid.status == "error"
    assert ("terminal" if terminal else "must belong") in invalid.error.message
    path = root / hub.result.path
    before = path.read_bytes()
    index_before = require_fresh_compiled_router(str(root))["artefact_index"]
    for field, value in (("default_parent", "workspace/other"), ("default_tags", ("bad-bypass",))):
        created = app.invoke(ArtefactCreateRequest("living/workspace", "Bypass", key="bypass",
            frontmatter=(FrontmatterField(field, value),)))
        edited = app.invoke(DocumentUpdateFrontmatterRequest(
            DocumentLocator(DocumentResource.ARTEFACT, "workspace/demo"), document_revision_at(path),
            (FrontmatterField(field, value),)))
        cleared = app.invoke(DocumentUpdateFrontmatterRequest(
            DocumentLocator(DocumentResource.ARTEFACT, "workspace/demo"), document_revision_at(path),
            (FrontmatterField(field, None),)))
        for result in (created, edited, cleared):
            assert result.status == "error", result
            assert result.effects == "none"
            assert "workspace.update-policy" in result.error.message
    assert path.read_bytes() == before
    assert not inspect_router_cache(root, verify_content=True).stale
    assert require_fresh_compiled_router(str(root))["artefact_index"] == index_before
    valid = app.invoke(WorkspaceUpdatePolicyRequest("workspace/demo", "workspace/demo", default_tags=("owned",)))
    assert valid.status == "ok", valid
    indexed = require_fresh_compiled_router(str(root))["artefact_index"]["workspace/demo"]
    assert indexed["default_parent"] == "workspace/demo"
    assert indexed["default_tags"] == ["owned"]


def test_metadata_cannot_relink_and_clear_preserves_binding_registry(command_vault_clone, tmp_path):
    import workspace_registry
    import configure
    from _application.workspace.update_metadata import WorkspaceMetadataLink

    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    vault_registry.register(root, "demo-brain")
    app = application(root, workspace)
    assert app.invoke(WorkspaceSetupRequest()).status == "ok"
    original = read_workspace_manifest(workspace)
    registry = workspace_registry.load_registry(root)
    for name in ("workspace", " workspace "):
        rejected = app.invoke(WorkspaceUpdateMetadataRequest(links=(WorkspaceMetadataLink(name, "other"),)))
        assert rejected.status == "error", rejected
        assert rejected.effects == "none"
        assert read_workspace_manifest(workspace) == original
    direct = configure.configure_workspace_metadata_action(root, workspace_dir=workspace, tags=[],
        links=["workspace=other"], clear_tags=False, clear_links=False)
    assert direct["status"] == "error"
    assert app.invoke(WorkspaceUpdateMetadataRequest(links=(WorkspaceMetadataLink("repository", "repo"),))).status == "ok"
    assert app.invoke(WorkspaceUpdateMetadataRequest(clear_links=True)).status == "ok"
    assert read_workspace_manifest(workspace) == original
    assert workspace_registry.load_registry(root) == registry
    retry = app.invoke(WorkspaceSetupRequest())
    assert retry.status == "ok" and retry.committed_effects == ()


@pytest.mark.parametrize("alias", ["brain-b", "missing-alias"])
def test_session_rejects_manifest_bound_to_another_or_missing_brain(command_vault_clone, tmp_path, alias):
    from session import build_session_model
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    root = command_vault_clone.vault_root
    assert application(root).invoke(WorkspaceEnsureRegistrationRequest("same-key")).status == "ok"
    other = tmp_path / "other-brain"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core/VERSION").write_text("0.1.0\n")
    vault_registry.register(root, "brain-a")
    vault_registry.register(other, "brain-b")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manifest = {"brain": alias, "slug": "repo", "links": {"workspace": "same-key"}}
    save_workspace_manifest_data(workspace, manifest)
    view = require_fresh_compiled_router(str(root))
    model = build_session_model(view, str(root), workspace_dir=str(workspace))
    assert model["workspace_configuration"]["binding_status"] == "configured_invalid"
    assert "selected Brain" in model["workspace_configuration"]["guidance"]
    assert model.get("workspace_record") is None
    save_workspace_manifest_data(workspace, {**manifest, "brain": "brain-a"})
    valid = build_session_model(view, str(root), workspace_dir=str(workspace))
    assert valid["workspace_configuration"]["binding_status"] == "valid"
