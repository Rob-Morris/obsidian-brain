"""Semantic upgrade admission uses real source files and leaves them unchanged."""

from pathlib import Path

import compile_router
import pytest

from _common import serialize_frontmatter
from _lifecycle import workspace_upgrade


def put(root, path, **fields):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialize_frontmatter(fields, body="# Deliberately unrelated title\n"))
    return target


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def vault(command_vault_clone):
    root = command_vault_clone.vault_root
    put(root, "Workspaces/Alpha.md", type="living/workspace", key="alpha", status="active")
    put(root, "Projects/Local.md", type="living/project", key="local", status="active",
        workspace="workspace/alpha")
    return root


def test_empty_workspace_semantics_need_no_compiler(tmp_path, monkeypatch):
    put(tmp_path, "Projects/Global.md", type="living/project", key="global", parent="project/missing")
    monkeypatch.delattr(compile_router, "compile")
    before = snapshot(tmp_path)
    assert workspace_upgrade.preflight_workspace_upgrade(tmp_path)["outcome"] == "ready"
    assert snapshot(tmp_path) == before


def test_root_policy_global_orphan_and_historical_tags_are_allowed(vault):
    put(vault, "Workspaces/Alpha.md", type="living/workspace", key="alpha", status="active",
        default_parent="project/local", default_tags=["workspace/missing"])
    put(vault, "Projects/Orphan.md", type="living/project", key="orphan", parent="project/missing",
        tags=["workspace/historical"])
    before = snapshot(vault)
    result = workspace_upgrade.preflight_workspace_upgrade(vault)
    assert result["outcome"] == "ready", result
    assert result["total_count"] == 0
    assert snapshot(vault) == before


@pytest.mark.parametrize("location,status", [("Workspaces", "active"),
    ("Workspaces", "completed"), ("_Archive/Workspaces", "completed")])
def test_parented_hub_blocks_across_active_terminal_and_archive(vault, location, status):
    (vault / "Workspaces/Alpha.md").unlink()
    put(vault, f"{location}/Alpha.md", type="living/workspace", key="alpha", status=status,
        parent="project/local")
    before = snapshot(vault)
    result = workspace_upgrade.preflight_workspace_upgrade(vault)
    assert result["outcome"] == "blocked"
    assert any(item.get("evidence", {}).get("cause") == "workspace_parent"
               for item in result["findings"])
    assert "explicit approval" in result["message"]
    assert snapshot(vault) == before


@pytest.mark.parametrize("workspace", [None, "workspace/beta"])
def test_conflicting_primary_membership_blocks_without_inference(vault, workspace):
    put(vault, "Workspaces/Beta.md", type="living/workspace", key="beta")
    put(vault, "Projects/Child.md", type="living/project", key="child", parent="project/local",
        tags=["workspace/alpha"], **({"workspace": workspace} if workspace else {}))
    result = workspace_upgrade.preflight_workspace_upgrade(vault)
    assert result["outcome"] == "blocked"
    assert any(item.get("evidence", {}).get("cause") == "workspace_mismatch"
               for item in result["findings"])


def test_invalid_default_policy_blocks(vault):
    put(vault, "Workspaces/Alpha.md", type="living/workspace", key="alpha",
        default_parent="project/missing")
    result = workspace_upgrade.preflight_workspace_upgrade(vault)
    assert result["outcome"] == "blocked"
    assert any(item["code"] == "workspace_policy_invalid" for item in result["findings"])


def test_archived_root_without_members_is_valid(vault):
    (vault / "Workspaces/Alpha.md").unlink()
    (vault / "Projects/Local.md").unlink()
    put(vault, "_Archive/Workspaces/Alpha.md", type="living/workspace", key="alpha",
        workspace="workspace/alpha", status="completed")
    assert workspace_upgrade.preflight_workspace_upgrade(vault)["outcome"] == "ready"


@pytest.mark.parametrize("reference", ["not-canonical", "workspace/unrelated-title"])
def test_membership_requires_exact_canonical_identity(vault, reference):
    put(vault, "Projects/Member.md", type="living/project", key="member", workspace=reference)
    result = workspace_upgrade.preflight_workspace_upgrade(vault)
    assert result["outcome"] == "blocked"
    assert any(item["code"].startswith("workspace_reference_") for item in result["findings"])


def test_bounded_findings_keep_total_and_pin_read_only_check(vault):
    for number in range(25):
        put(vault, f"Projects/Bad{number}.md", type="living/project", key=f"bad{number}",
            workspace="workspace/missing")
    result = workspace_upgrade.preflight_workspace_upgrade(vault)
    assert len(result["findings"]) == 20
    assert result["total_count"] == 25
    assert result["read_only_check"]["argv"][:3] == ["brain", "--vault", str(vault.resolve())]
    assert result["read_only_check"]["arguments"] == {"check": "workspace_contract"}


def test_no_folder_metadata_registry_or_persistence(vault, monkeypatch):
    import _bootstrap.workspace_binding as binding
    import _bootstrap.diagnostics as diagnostics

    def forbidden(*args, **kwargs):
        pytest.fail("preflight crossed the folder metadata or persistence boundary")

    monkeypatch.setattr(binding, "load_workspace_manifest_state", forbidden)
    monkeypatch.setattr(diagnostics, "inspect_registry", forbidden)
    monkeypatch.setattr(compile_router, "persist_compiled_router", forbidden)
    original = Path.read_bytes
    before = snapshot(vault)

    def guarded(path):
        assert path.is_relative_to(vault)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    assert workspace_upgrade.preflight_workspace_upgrade(vault)["outcome"] == "ready"
    assert snapshot(vault) == before


@pytest.mark.parametrize("version", [(0, 73, 0), (1, 0, 0)])
def test_existing_contract_does_not_read_artefacts_again(tmp_path, monkeypatch, version):
    def forbidden(*_args, **_kwargs):
        pytest.fail("an already compatible Core does not need the ownership migration scan")

    monkeypatch.setattr(workspace_upgrade, "read_ownership_graph", forbidden)
    result = workspace_upgrade.preflight_workspace_upgrade(tmp_path, installed_version=version)
    assert result["outcome"] == "ready"
    assert result["total_count"] == 0


def test_prior_contract_still_requires_explicit_parent_repair(vault):
    put(vault, "Workspaces/Alpha.md", type="living/workspace", key="alpha",
        parent="project/local")
    result = workspace_upgrade.preflight_workspace_upgrade(vault, installed_version=(0, 72, 0))
    assert result["outcome"] == "blocked"
    assert any(item["evidence"]["cause"] == "workspace_parent" for item in result["findings"])
