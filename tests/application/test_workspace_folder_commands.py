"""Canonical workspace commands select folders independently of shared identity."""

from dataclasses import replace

import pytest
import vault_registry
import workspace_registry
from _bootstrap.workspace_binding import read_workspace_manifest
from _application.workspace.create import WorkspaceCreateRequest
from _application.workspace.register import WorkspaceRegisterRequest
from _application.workspace.rebind import WorkspaceRebindRequest
from _application.workspace.unregister import WorkspaceUnregisterRequest
from _application.workspace.read import WorkspaceReadRequest
from test_workspace_membership_policy import application


def register_folder(root, folder, slug):
    folder.mkdir(exist_ok=True)
    app = application(root, folder)
    result = app.invoke(WorkspaceRegisterRequest(slug=slug))
    assert result.status == "ok", result
    return app


def test_two_folders_share_identity_and_repeat_omits_slug(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    assert application(root).invoke(WorkspaceCreateRequest("shared")).status == "ok"
    one, two = tmp_path / "one", tmp_path / "two"
    first = register_folder(root, one, "shared")
    register_folder(root, two, "shared")
    again = first.invoke(WorkspaceRegisterRequest())
    assert again.status == "ok" and again.committed_effects == (), again
    rows = workspace_registry.load_registry_strict(root)
    assert set(rows) == {str(one), str(two)}
    assert {row["slug"] for row in rows.values()} == {"shared"}
    read = application(root).invoke(WorkspaceReadRequest("shared"))
    assert read.status == "ok", read
    assert {item.path for item in read.result.locations} == {str(one), str(two)}
    assert read_workspace_manifest(one) == {"schema_version": 1, "brain": "selected-brain", "slug": "shared"}


def test_register_rejects_retarget_and_force_without_effects(command_vault_clone, tmp_path):
    from _application.workspace.register import decode
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    for slug in ("source", "target"):
        assert application(root).invoke(WorkspaceCreateRequest(slug)).status == "ok"
    folder = tmp_path / "folder"
    app = register_folder(root, folder, "source")
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    result = app.invoke(WorkspaceRegisterRequest(slug="target"))
    assert result.status == "error" and result.effects == "none", result
    assert "rebind" in result.error.message
    assert (folder / ".brain/local/workspace.yaml").read_bytes() == before
    with pytest.raises(ValueError, match="unexpected fields: force"):
        decode({"slug": "target", "force": True})


def test_rebind_one_folder_keeps_both_hubs_and_other_row(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    hubs = [application(root).invoke(WorkspaceCreateRequest(slug)).result.path for slug in ("source", "target")]
    one, two = tmp_path / "one", tmp_path / "two"
    first = register_folder(root, one, "source")
    register_folder(root, two, "source")
    result = first.invoke(WorkspaceRebindRequest("selected-brain", "target"))
    assert result.status == "ok", result
    rows = workspace_registry.load_registry_strict(root)
    assert rows[str(one)]["slug"] == "target"
    assert rows[str(two)]["slug"] == "source"
    assert read_workspace_manifest(one)["slug"] == "target"
    assert all((root / hub).is_file() for hub in hubs)


@pytest.mark.parametrize("state", ["matching", "missing", "malformed", "different", "future", "boolean-version", "invalid-version", "redundant-link"])
def test_unregister_only_selected_folder_with_truthful_followup(command_vault_clone, tmp_path, state):
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    assert application(root).invoke(WorkspaceCreateRequest("shared")).status == "ok"
    one, two = tmp_path / "one", tmp_path / "two"
    first = register_folder(root, one, "shared")
    register_folder(root, two, "shared")
    path = one / ".brain/local/workspace.yaml"
    if state == "missing":
        path.unlink()
    elif state == "malformed":
        path.write_text("links: [not: valid\n")
    elif state == "different":
        path.write_text("schema_version: 1\nbrain: selected-brain\nslug: different\n")
    elif state in {"future", "boolean-version", "invalid-version", "redundant-link"}:
        version = {"future": "2", "boolean-version": "true", "invalid-version": "broken", "redundant-link": "1"}[state]
        path.write_text(f"schema_version: {version}\nbrain: selected-brain\nslug: shared\n"
                        + ("links:\n  workspace: shared\n" if state == "redundant-link" else ""))
    before = path.read_bytes() if path.exists() else None
    result = first.invoke(WorkspaceUnregisterRequest())
    assert result.status == "ok", result
    rows = workspace_registry.load_registry_strict(root)
    assert str(one) not in rows and rows[str(two)]["slug"] == "shared"
    if state == "matching":
        assert read_workspace_manifest(one) == {"schema_version": 1}
        assert result.warnings == ()
    else:
        assert (path.read_bytes() if path.exists() else None) == before
        assert result.warnings[0].code.value == "follow_up_required"


def test_canonical_read_without_locations(command_vault_clone):
    root = command_vault_clone.vault_root
    assert application(root).invoke(WorkspaceCreateRequest("empty")).status == "ok"
    result = application(root).invoke(WorkspaceReadRequest("empty"))
    assert result.status == "ok", result
    assert result.result.locations == ()


@pytest.mark.parametrize("source_slug,target_slug", [("source", "target"), ("shared", "shared")])
def test_rebind_across_brains_moves_only_selected_registration(command_vault_clone, tmp_path, source_slug, target_slug):
    import shutil
    root = command_vault_clone.vault_root
    other = tmp_path / "other-brain"
    shutil.copytree(root, other)
    vault_registry.register(root, "target-brain")
    vault_registry.register(other, "source-brain")
    assert application(root).invoke(WorkspaceCreateRequest(target_slug)).status == "ok"
    assert application(other).invoke(WorkspaceCreateRequest(source_slug)).status == "ok"
    one, two = tmp_path / "one", tmp_path / "two"
    register_folder(other, one, source_slug)
    register_folder(other, two, source_slug)
    result = application(root, one).invoke(WorkspaceRebindRequest("target-brain", target_slug))
    assert result.status == "ok", result
    assert set(workspace_registry.load_registry_strict(other)) == {str(two)}
    assert workspace_registry.load_registry_strict(root)[str(one)]["slug"] == target_slug
    assert read_workspace_manifest(one)["brain"] == "target-brain"


def test_rebind_refuses_conflicting_defaults_without_changing_binding(command_vault_clone, tmp_path):
    from _bootstrap.workspace_binding import save_workspace_manifest_data
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    for slug in ("source", "target"):
        assert application(root).invoke(WorkspaceCreateRequest(slug)).status == "ok"
    folder = tmp_path / "folder"
    app = register_folder(root, folder, "source")
    manifest = read_workspace_manifest(folder)
    save_workspace_manifest_data(folder, {**manifest, "defaults": {"parent": "workspace/source", "tags": ["keep"]}})
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    rows = workspace_registry.load_registry_strict(root)
    result = app.invoke(WorkspaceRebindRequest("selected-brain", "target"))
    assert result.status == "error" and result.effects == "none", result
    assert "must belong" in result.error.message
    assert (folder / ".brain/local/workspace.yaml").read_bytes() == before
    assert workspace_registry.load_registry_strict(root) == rows


def test_first_registration_retains_exact_unlinked_legacy_bytes(command_vault_clone, tmp_path):
    import base64
    import json
    from _bootstrap.workspace_recovery import RECOVERY_REL
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    assert application(root).invoke(WorkspaceCreateRequest("canonical")).status == "ok"
    folder = tmp_path / "folder"
    local = folder / ".brain/local"
    local.mkdir(parents=True)
    original = b"# Keep original formatting\r\nslug: old-local-name\r\nlinks:\r\n  repository: keep\r\ndefaults:\r\n  tags: [keep]\r\n"
    (local / "workspace.yaml").write_bytes(original)
    result = application(root, folder).invoke(WorkspaceRegisterRequest(slug="canonical"))
    assert result.status == "ok", result
    backup = folder / RECOVERY_REL
    evidence = json.loads(backup.read_text())
    assert base64.b64decode(evidence["before_base64"]) == original
    assert read_workspace_manifest(folder)["slug"] == "canonical"
    assert read_workspace_manifest(folder)["links"] == {"repository": "keep"}
    retained = backup.read_bytes()
    again = application(root, folder).invoke(WorkspaceRegisterRequest())
    assert again.status == "ok" and again.committed_effects == (), again
    assert backup.read_bytes() == retained


def test_legacy_backup_collision_refuses_without_overwrite_or_registration(command_vault_clone, tmp_path):
    from _bootstrap.workspace_recovery import RECOVERY_REL
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    assert application(root).invoke(WorkspaceCreateRequest("canonical")).status == "ok"
    folder = tmp_path / "folder"
    local = folder / ".brain/local"
    local.mkdir(parents=True)
    original = b"slug: old-local-name\n"
    (local / "workspace.yaml").write_bytes(original)
    backup = folder / RECOVERY_REL
    existing = b'{"schema":"brain.workspace-schema-recovery/1","before_base64":"different"}\n'
    backup.write_bytes(existing)
    before = workspace_registry.load_registry_strict(root)
    result = application(root, folder).invoke(WorkspaceRegisterRequest(slug="canonical"))
    assert result.status == "error" and result.effects == "none", result
    assert "different original" in result.error.message
    assert backup.read_bytes() == existing
    assert (local / "workspace.yaml").read_bytes() == original
    assert workspace_registry.load_registry_strict(root) == before


from pytest_bdd import given, scenarios, then, when

scenarios("../features/workspace_registration.feature")


@given("a selected Brain and two unregistered local folders", target_fixture="workspace_contract")
def selected_workspace_contract(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    vault_registry.register(root, "selected-brain")
    folders = (tmp_path / "one", tmp_path / "two")
    for folder in folders:
        folder.mkdir()
    return {"root": root, "folders": folders, "hubs": {}}


@given("an explicitly created shared workspace")
def create_shared_workspace(workspace_contract):
    result = application(workspace_contract["root"]).invoke(WorkspaceCreateRequest("shared"))
    assert result.status == "ok", result
    workspace_contract["hubs"]["shared"] = result.result.path


@given("an explicitly created target workspace")
def create_target_workspace(workspace_contract):
    result = application(workspace_contract["root"]).invoke(WorkspaceCreateRequest("target"))
    assert result.status == "ok", result
    workspace_contract["hubs"]["target"] = result.result.path


@when("the first folder is registered against a missing workspace")
def register_missing_workspace(workspace_contract):
    root, folders = workspace_contract["root"], workspace_contract["folders"]
    workspace_contract["before"] = workspace_registry.load_registry_strict(root)
    workspace_contract["result"] = application(root, folders[0]).invoke(WorkspaceRegisterRequest(slug="missing"))


@then("registration refuses without folder or registry effects and names the creation command")
def missing_workspace_refusal(workspace_contract):
    result = workspace_contract["result"]
    assert result.status == "error" and result.effects == "none", result
    assert result.error.next_action.command_id == "workspace.create"
    assert not (workspace_contract["folders"][0] / ".brain").exists()
    assert workspace_registry.load_registry_strict(workspace_contract["root"]) == workspace_contract["before"]


@when("the first folder is registered without a slug")
def register_without_identity(workspace_contract):
    workspace_contract["result"] = application(workspace_contract["root"], workspace_contract["folders"][0]).invoke(WorkspaceRegisterRequest())


@then("registration refuses without inferring identity from the folder name")
def explicit_identity_refusal(workspace_contract):
    result = workspace_contract["result"]
    assert result.status == "error" and result.effects == "none", result
    assert "explicit" in result.error.message and "slug" in result.error.message
    assert not (workspace_contract["folders"][0] / ".brain").exists()


@given("both folders registered against that workspace")
@when("both folders are registered against that workspace")
def register_both_folders(workspace_contract):
    for folder in workspace_contract["folders"]:
        register_folder(workspace_contract["root"], folder, "shared")


@when("the first registration is repeated without a slug")
def repeat_registration(workspace_contract):
    result = application(workspace_contract["root"], workspace_contract["folders"][0]).invoke(WorkspaceRegisterRequest())
    assert result.status == "ok" and result.committed_effects == (), result


@then("both folder registrations retain the same canonical identity")
def shared_folder_identity(workspace_contract):
    rows = workspace_registry.load_registry_strict(workspace_contract["root"])
    assert set(rows) == {str(folder) for folder in workspace_contract["folders"]}
    assert {row["slug"] for row in rows.values()} == {"shared"}


@when("the first folder is unregistered")
def unregister_first_folder(workspace_contract):
    workspace_contract["result"] = application(workspace_contract["root"], workspace_contract["folders"][0]).invoke(WorkspaceUnregisterRequest())


@then("only the selected folder is unlinked and the shared workspace remains")
def one_folder_unlinked(workspace_contract):
    result = workspace_contract["result"]
    assert result.status == "ok", result
    first, second = workspace_contract["folders"]
    assert read_workspace_manifest(first) == {"schema_version": 1}
    assert set(workspace_registry.load_registry_strict(workspace_contract["root"])) == {str(second)}
    assert (workspace_contract["root"] / workspace_contract["hubs"]["shared"]).is_file()


@when("the first folder is rebound to that target")
def rebind_first_folder(workspace_contract):
    workspace_contract["result"] = application(workspace_contract["root"], workspace_contract["folders"][0]).invoke(WorkspaceRebindRequest("selected-brain", "target"))


@then("the other folder and both canonical workspaces remain")
def one_folder_rebound(workspace_contract):
    result = workspace_contract["result"]
    assert result.status == "ok", result
    first, second = workspace_contract["folders"]
    assert read_workspace_manifest(first)["slug"] == "target"
    assert read_workspace_manifest(second)["slug"] == "shared"
    rows = workspace_registry.load_registry_strict(workspace_contract["root"])
    assert rows[str(first)]["slug"] == "target" and rows[str(second)]["slug"] == "shared"
    assert all((workspace_contract["root"] / hub).is_file() for hub in workspace_contract["hubs"].values())


@pytest.mark.parametrize("operation", ["register", "rebind"])
def test_conflicting_claim_in_another_brain_refuses_selected_folder(command_vault_clone, tmp_path, operation):
    import shutil
    root = command_vault_clone.vault_root
    other = tmp_path / "other-brain"
    shutil.copytree(root, other)
    vault_registry.register(root, "selected-brain")
    vault_registry.register(other, "foreign-brain")
    for slug in ("source", "target"):
        assert application(root).invoke(WorkspaceCreateRequest(slug)).status == "ok"
    folder = tmp_path / "folder"
    if operation == "rebind":
        app = register_folder(root, folder, "source")
        before = (folder / ".brain/local/workspace.yaml").read_bytes()
        request = WorkspaceRebindRequest("selected-brain", "target")
    else:
        folder.mkdir()
        app = application(root, folder)
        before = None
        request = WorkspaceRegisterRequest(slug="target")
    workspace_registry.register_workspace(other, "foreign", folder)
    rows = workspace_registry.load_registry_strict(root)
    result = app.invoke(request)
    assert result.status == "error" and result.effects == "none", result
    assert "Conflicting workspace registration claims" in result.error.message
    path = folder / ".brain/local/workspace.yaml"
    assert (path.read_bytes() if path.exists() else None) == before
    assert workspace_registry.load_registry_strict(root) == rows
    assert workspace_registry.load_registry_strict(other)[str(folder)]["slug"] == "foreign"


@pytest.mark.parametrize('legacy', [False, True])
def test_foreign_claim_after_registry_commit_prevents_all_local_writes(command_vault_clone, tmp_path, monkeypatch, legacy):
    root = command_vault_clone.vault_root
    foreign = tmp_path / 'foreign-brain'
    version = foreign / '.brain-core/VERSION'
    version.parent.mkdir(parents=True)
    version.write_text('0.72.0\n')
    vault_registry.register(root, 'selected-brain')
    vault_registry.register(foreign, 'foreign-brain')
    assert application(root).invoke(WorkspaceCreateRequest('shared')).status == 'ok'
    folder = tmp_path / 'folder'
    folder.mkdir()
    if legacy:
        manifest = folder / '.brain/local/workspace.yaml'
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b'slug: local-only\nunknown: preserved\n')
    before = {str(path.relative_to(folder)): path.read_bytes()
              for path in folder.rglob('*') if path.is_file() and path.name != 'mutation.lock'}
    original = workspace_registry.register_workspace

    def commit_then_claim(vault, slug, path, **kwargs):
        result = original(vault, slug, path, **kwargs)
        if vault == root:
            # Simulate an outside writer: it does not acquire the coordination lock.
            original(foreign, 'foreign', folder)
        return result

    monkeypatch.setattr(workspace_registry, 'register_workspace', commit_then_claim)
    result = application(root, folder).invoke(WorkspaceRegisterRequest(slug='shared'))
    assert result.status == 'partial', result
    assert 'Conflicting workspace registration claims' in result.error.message
    assert [effect.kind for effect in result.committed_effects] == ['workspace.path-registered']
    assert workspace_registry.load_registry_strict(root)[str(folder)]['slug'] == 'shared'
    assert workspace_registry.load_registry_strict(foreign)[str(folder)]['slug'] == 'foreign'
    assert before == {str(path.relative_to(folder)): path.read_bytes()
                      for path in folder.rglob('*') if path.is_file() and path.name != 'mutation.lock'}
    assert not (folder / '.brain/local/workspace.pre-schema-1.json').exists()
