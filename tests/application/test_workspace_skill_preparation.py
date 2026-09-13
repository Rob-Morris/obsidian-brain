"""Prepared workspace and skill actions reject drift before their first write."""

from dataclasses import replace
from pathlib import Path
import subprocess

from _application.skill.add_git import SkillAddGitRequest, execute as add_skill
from _application.skill.update import SkillUpdateRequest, execute as update_skill
from _application.skill._preparation import prepare_skill
from _application.workspace._preparation import prepare_workspace
from _application.workspace.configure_bootstrap import WorkspaceConfigureBootstrapRequest, execute as configure_bootstrap
from _application.workspace.register import WorkspaceRegisterRequest, execute as register_workspace
from _application.workspace.setup import WorkspaceSetupRequest, execute as setup_workspace
from command_application import application_for
from test_skill_library import allow_local_git, _source_repo, _advance
from _skill_library import add_git_skill
from _skill_library.tracking import load_tracking
import vault_registry


class MatchingAdmission:
    def __init__(self, binding):
        self.requires_binding = True
        self.frozen_inputs = binding.frozen_inputs
        self.expected = binding.digest
        self.calls = 0

    def admit(self, binding):
        if binding.digest != self.expected:
            raise ValueError("prepared operation changed")
        self.calls += 1


def _workspace_context(root, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return application_for(root, workspace_dir=workspace)._context, workspace


def test_workspace_registration_uses_same_prepared_target_and_admits_once(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    context, workspace = _workspace_context(root, tmp_path)
    request = WorkspaceRegisterRequest("prepared")
    binding = prepare_workspace(context, request)
    registry = root / ".brain/local/workspaces.json"
    before = registry.read_bytes() if registry.exists() else None
    admission = MatchingAdmission(binding)
    assert (registry.read_bytes() if registry.exists() else None) == before
    result = register_workspace(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert str(workspace) in registry.read_text()
    assert admission.calls == 1


def test_workspace_selector_drift_cannot_write_other_workspace_registration(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    context, _workspace = _workspace_context(root, tmp_path)
    request = WorkspaceRegisterRequest("prepared")
    admission = MatchingAdmission(prepare_workspace(context, request))
    other = tmp_path / "other-workspace"
    other.mkdir()
    registry = root / ".brain/local/workspaces.json"
    before = registry.read_bytes() if registry.exists() else None
    result = register_workspace(replace(context, workspace_dir=other, admission=admission), request)
    assert result.status == "error"
    assert admission.calls == 0
    assert (registry.read_bytes() if registry.exists() else None) == before


def test_setup_pins_git_exclude_before_manifest_creation(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    context, workspace = _workspace_context(root, tmp_path)
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    vault_registry.register(root, "consent-brain")
    request = WorkspaceSetupRequest("consent-brain")
    admission = MatchingAdmission(prepare_workspace(context, request))
    exclude = workspace / ".git/info/exclude"
    exclude.write_text("changed since preparation\n")
    result = setup_workspace(replace(context, admission=admission), request)
    assert result.status == "error"
    assert not (workspace / ".brain/local/workspace.yaml").exists()
    assert exclude.read_text() == "changed since preparation\n"
    assert admission.calls == 0


def test_bootstrap_many_files_share_one_admission(command_vault_clone, tmp_path):
    context, workspace = _workspace_context(command_vault_clone.vault_root, tmp_path)
    request = WorkspaceConfigureBootstrapRequest("all")
    admission = MatchingAdmission(prepare_workspace(context, request))
    result = configure_bootstrap(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert admission.calls == 1
    assert (workspace / "AGENTS.md").is_file()
    assert (workspace / "CLAUDE.md").is_file()
    assert (workspace / ".grok/rules/brain.md").is_file()


def test_git_add_uses_prepared_commit_after_branch_moves(command_vault_clone, tmp_path, allow_local_git):
    root = command_vault_clone.vault_root
    repository, first = _source_repo(tmp_path, "prepared-skill")
    context = application_for(root)._context
    request = SkillAddGitRequest(str(repository), "skills/prepared-skill")
    binding = prepare_skill(context, request)
    assert binding.frozen_inputs["source"]["resolved_commit"] == first
    assert not (root / "_Config/Skills/prepared-skill").exists()
    _advance(repository, "prepared-skill", "version two")
    admission = MatchingAdmission(binding)
    result = add_skill(replace(context, admission=admission), request)
    assert result.status == "ok", result
    assert admission.calls == 1
    assert "version one" in (root / "_Config/Skills/prepared-skill/SKILL.md").read_text()
    assert load_tracking(root)["managed"]["prepared-skill"]["configured_ref"] == "HEAD"


def test_git_update_rejects_changed_package_without_tracking_or_backup_effects(command_vault_clone, tmp_path, allow_local_git):
    root = command_vault_clone.vault_root
    repository, _first = _source_repo(tmp_path, "prepared-skill")
    add_git_skill(root, repository=str(repository), skill_path="skills/prepared-skill")
    _advance(repository, "prepared-skill", "version two")
    context = application_for(root)._context
    request = SkillUpdateRequest("prepared-skill", replace_conflict=True)
    admission = MatchingAdmission(prepare_skill(context, request))
    skill = root / "_Config/Skills/prepared-skill/SKILL.md"
    skill.write_text(skill.read_text() + "\nlocal change after consent\n")
    tracking_before = (root / ".brain/skill-sources.json").read_bytes()
    result = update_skill(replace(context, admission=admission), request)
    assert result.status == "error"
    assert admission.calls == 0
    assert (root / ".brain/skill-sources.json").read_bytes() == tracking_before
    assert "local change after consent" in skill.read_text()
    assert not (root / ".brain/skill-backups").exists()
    assert not (root / ".brain/skill-conflicts").exists()
