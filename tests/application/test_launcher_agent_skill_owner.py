"""Typed launcher ownership for active-Brain agent skill adapters."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.agent_skill import (
    AgentSkillAction,
    AgentSkillClient,
    AgentSkillConfigureRequest,
    AgentSkillMutationStatus,
    SkillExposeRequest,
    SkillExposureScope,
    SkillUnexposeRequest,
)
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ErrorCode, ReceiptState
from _launcher.invocation import LauncherInvocation
from _launcher.owners import LAUNCHER_OWNERS
from _bootstrap import agent_skills


NOW = datetime.fromisoformat("2026-08-10T06:00:00+10:00")


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"

    def __init__(self, available=True):
        self.available = available


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _invocation(
    tmp_path,
    *,
    home_dir=None,
    provider=True,
    provider_available=True,
    dry_run=False,
    receipts=None,
    current_vault=None,
    workspace_dir=None,
):
    providers = ((_CallerFilesystem(provider_available),) if provider else ())
    context = LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings(providers),
        correlation_id="corr-agent-skill",
        invocation_id="inv-agent-skill",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=(home_dir or tmp_path).resolve(),
        cli_version="2.0.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=current_vault.resolve() if current_vault else None,
        workspace_dir=workspace_dir.resolve() if workspace_dir else None,
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _skill_dir(home, client):
    return home / f".{client}" / "skills" / agent_skills.ADAPTER_SKILL


def _brain_skill(vault, name, body, *, user=False):
    base = vault / ("_Config/Skills" if user else ".brain-core/skills") / name
    base.mkdir(parents=True)
    (base / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Example\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_agent_skill_owner_matches_machine_global_contract():
    entry = next(
        item
        for item in LAUNCHER_CATALOGUE.entries
        if item.command_id == "agent-skill.configure"
    )
    owner = next(
        item
        for item in LAUNCHER_OWNERS.entries
        if item.command_id == "agent-skill.configure"
    )

    assert entry.owner_ref == owner.owner_ref == "_launcher.agent_skill:configure"
    assert entry.authority == "operator"
    assert entry.effect_class == "machine_mutation"
    assert entry.retry_class == "receipt_required"
    assert entry.required_providers == ("caller_filesystem",)
    assert {item.projection: item.supported for item in entry.projections} == {
        "cli": True,
        "launcher": True,
        "mcp": False,
        "python": False,
        "script": False,
    }


def test_agent_skill_request_rejects_open_strings_and_remove_replace():
    with pytest.raises(ValueError, match="client must be closed"):
        AgentSkillConfigureRequest(client="claude")
    with pytest.raises(ValueError, match="action must be closed"):
        AgentSkillConfigureRequest(action="remove")
    with pytest.raises(ValueError, match="applies only to configure"):
        AgentSkillConfigureRequest(
            action=AgentSkillAction.REMOVE,
            replace=True,
        )


def test_agent_skill_requires_an_available_caller_filesystem(tmp_path):
    request = AgentSkillConfigureRequest(client=AgentSkillClient.CLAUDE)

    missing = _invocation(tmp_path, provider=False).invoke(request)
    unavailable = _invocation(
        tmp_path,
        provider_available=False,
    ).invoke(request)

    assert missing.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing.error.details.missing == ("provider:caller_filesystem",)
    assert unavailable.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert unavailable.error.details.missing == ("capability:caller_filesystem",)
    assert not (tmp_path / ".claude").exists()


def test_agent_skill_dry_run_uses_real_plan_without_writing(tmp_path):
    receipts = _Receipts()

    result = _invocation(
        tmp_path,
        dry_run=True,
        receipts=receipts,
    ).invoke(AgentSkillConfigureRequest())

    assert tuple(step.client for step in result.result.steps) == (
        AgentSkillClient.CLAUDE,
        AgentSkillClient.CODEX,
        AgentSkillClient.GROK,
    )
    assert all(
        step.status is AgentSkillMutationStatus.PLANNED
        for step in result.result.steps
    )
    assert result.committed_effects == ()
    assert receipts.values[-1].state is ReceiptState.NONE
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


def test_agent_skill_configure_is_typed_idempotent_and_receipted(tmp_path):
    receipts = _Receipts()
    invocation = _invocation(tmp_path, receipts=receipts)
    request = AgentSkillConfigureRequest(client=AgentSkillClient.CLAUDE)

    installed = invocation.invoke(request)
    repeated = invocation.invoke(request)

    assert installed.result.steps[0].status is AgentSkillMutationStatus.CHANGED
    assert tuple(effect.subject for effect in installed.committed_effects) == (
        "agent-skill:claude:shaping",
    )
    assert repeated.result.steps[0].status is AgentSkillMutationStatus.NOOP
    assert repeated.committed_effects == ()
    assert receipts.values[-2].state is ReceiptState.COMMITTED
    assert receipts.values[-1].state is ReceiptState.COMMITTED
    assert (_skill_dir(tmp_path, "claude") / "SKILL.md").is_file()


def test_generic_global_exposure_loads_effective_skill_without_copying_package(
    tmp_path,
):
    vault = tmp_path / "vault"
    _brain_skill(vault, "example", "CORE BODY")
    _brain_skill(vault, "example", "USER BODY", user=True)
    invocation = _invocation(tmp_path, current_vault=vault)

    exposed = invocation.invoke(
        SkillExposeRequest("example", client=AgentSkillClient.CLAUDE)
    )
    destination = tmp_path / ".claude/skills/example"
    content = (destination / "SKILL.md").read_text(encoding="utf-8")
    marker = (destination / agent_skills.MARKER_FILE).read_text(encoding="utf-8")

    assert exposed.result.scope is SkillExposureScope.GLOBAL
    assert "resource.read" in content
    assert 'reference="example"' in content
    assert "USER BODY" not in content
    assert '"skill": "example"' in marker
    assert {path.name for path in destination.iterdir()} == {
        "SKILL.md",
        agent_skills.MARKER_FILE,
    }

    removed = invocation.invoke(
        SkillUnexposeRequest("example", client=AgentSkillClient.CLAUDE)
    )
    assert removed.result.steps[0].status is AgentSkillMutationStatus.CHANGED
    assert not destination.exists()


def test_generic_shaping_exposure_adopts_existing_adapter_without_rewrite(tmp_path):
    vault = tmp_path / "vault"
    _brain_skill(vault, "shaping", "CORE SHAPING")
    invocation = _invocation(tmp_path, current_vault=vault)
    configured = invocation.invoke(
        AgentSkillConfigureRequest(client=AgentSkillClient.CLAUDE)
    )
    destination = tmp_path / ".claude/skills/shaping"
    skill_before = (destination / "SKILL.md").read_bytes()
    marker_before = (destination / agent_skills.MARKER_FILE).read_bytes()

    exposed = invocation.invoke(
        SkillExposeRequest("shaping", client=AgentSkillClient.CLAUDE)
    )

    assert configured.status == "ok"
    assert exposed.result.steps[0].status is AgentSkillMutationStatus.NOOP
    assert (destination / "SKILL.md").read_bytes() == skill_before
    assert (destination / agent_skills.MARKER_FILE).read_bytes() == marker_before


@pytest.mark.parametrize("client", [AgentSkillClient.CODEX, AgentSkillClient.GROK])
def test_project_exposure_requires_matching_canonical_binding(
    tmp_path,
    monkeypatch,
    client,
):
    import vault_registry

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    vault = tmp_path / "vault"
    workspace = tmp_path / "project"
    workspace.mkdir()
    _brain_skill(vault, "example", "CORE BODY")
    (vault / ".brain-core/VERSION").write_text("0.62.0\n", encoding="utf-8")
    vault_registry.register(str(vault), "selected")
    manifest = workspace / ".brain/local/workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("brain: selected\nslug: project\n", encoding="utf-8")
    registry_before = vault_registry.registry_path()
    registry_bytes = Path(registry_before).read_bytes()
    manifest_bytes = manifest.read_bytes()
    invocation = _invocation(
        tmp_path,
        home_dir=home,
        current_vault=vault,
        workspace_dir=workspace,
    )
    result = invocation.invoke(
        SkillExposeRequest(
            "example",
            client=client,
            scope=SkillExposureScope.PROJECT,
        )
    )

    assert result.status == "ok"
    assert (workspace / f".{client.value}/skills/example/SKILL.md").is_file()
    assert manifest.read_bytes() == manifest_bytes
    assert Path(registry_before).read_bytes() == registry_bytes


def test_project_exposure_refuses_missing_binding_without_creating_one(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    vault = tmp_path / "vault"
    workspace = tmp_path / "project"
    workspace.mkdir()
    _brain_skill(vault, "example", "CORE BODY")

    result = _invocation(
        tmp_path,
        home_dir=home,
        current_vault=vault,
        workspace_dir=workspace,
    ).invoke(
        SkillExposeRequest(
            "example",
            client=AgentSkillClient.CODEX,
            scope=SkillExposureScope.PROJECT,
        )
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert not (workspace / ".brain/local/workspace.yaml").exists()
    assert not (workspace / ".codex").exists()


def test_project_exposure_uses_machine_default_without_creating_binding(
    tmp_path,
    monkeypatch,
):
    import vault_registry

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    vault = tmp_path / "vault"
    workspace = tmp_path / "project"
    workspace.mkdir()
    _brain_skill(vault, "example", "CORE BODY")
    (vault / ".brain-core/VERSION").write_text("0.62.0\n", encoding="utf-8")
    vault_registry.register(str(vault), "selected")
    vault_registry.set_default("selected")
    registry_path = vault_registry.registry_path()
    registry_bytes = Path(registry_path).read_bytes()

    result = _invocation(
        tmp_path,
        home_dir=home,
        current_vault=vault,
        workspace_dir=workspace,
    ).invoke(
        SkillExposeRequest(
            "example",
            client=AgentSkillClient.CODEX,
            scope=SkillExposureScope.PROJECT,
        )
    )

    assert result.status == "ok"
    assert (workspace / ".codex/skills/example/SKILL.md").is_file()
    assert not (workspace / ".brain/local/workspace.yaml").exists()
    assert Path(registry_path).read_bytes() == registry_bytes


def test_generic_exposure_apply_failure_is_unknown_not_no_effect(
    tmp_path,
    monkeypatch,
):
    vault = tmp_path / "vault"
    _brain_skill(vault, "example", "CORE BODY")
    original = agent_skills.install_prepared_skill_adapter

    def fail_apply(*args, dry_run=False, **kwargs):
        if not dry_run:
            raise OSError("simulated exposure ambiguity")
        return original(*args, dry_run=dry_run, **kwargs)

    monkeypatch.setattr(agent_skills, "install_prepared_skill_adapter", fail_apply)
    result = _invocation(tmp_path, current_vault=vault).invoke(
        SkillExposeRequest("example", client=AgentSkillClient.CLAUDE)
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"


def test_all_client_validation_completes_before_any_write(tmp_path):
    unmanaged = _skill_dir(tmp_path, "codex")
    unmanaged.mkdir(parents=True)
    (unmanaged / "SKILL.md").write_text("custom")

    result = _invocation(tmp_path).invoke(AgentSkillConfigureRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert "unmanaged skill" in result.error.message
    assert not (tmp_path / ".claude").exists()


def test_replace_reports_the_client_adapter_and_backup_effects(tmp_path):
    unmanaged = _skill_dir(tmp_path, "codex")
    unmanaged.mkdir(parents=True)
    (unmanaged / "SKILL.md").write_text("custom")

    result = _invocation(tmp_path).invoke(
        AgentSkillConfigureRequest(
            client=AgentSkillClient.CODEX,
            replace=True,
        )
    )

    step = result.result.steps[0]
    assert step.status is AgentSkillMutationStatus.CHANGED
    assert step.backup_path is not None
    assert Path(step.backup_path).is_dir()
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "agent-skill:codex:shaping",
        f"agent-skill-backup:{step.backup_path}",
    )


def test_remove_plans_and_applies_only_a_managed_adapter(tmp_path):
    agent_skills.configure_agent_skill_adapters(
        home_dir=tmp_path,
        client="claude",
    )
    request = AgentSkillConfigureRequest(
        client=AgentSkillClient.CLAUDE,
        action=AgentSkillAction.REMOVE,
    )

    planned = _invocation(tmp_path, dry_run=True).invoke(request)
    removed = _invocation(tmp_path).invoke(request)

    assert planned.result.steps[0].status is AgentSkillMutationStatus.PLANNED
    assert removed.result.steps[0].status is AgentSkillMutationStatus.CHANGED
    assert tuple(effect.subject for effect in removed.committed_effects) == (
        "agent-skill:claude:shaping",
    )
    assert not _skill_dir(tmp_path, "claude").exists()


def test_apply_failure_is_non_retryable_unknown_outcome(tmp_path, monkeypatch):
    original = agent_skills.install_prepared_skill_adapter

    def fail_apply(*args, dry_run=False, **kwargs):
        if not dry_run:
            raise OSError("simulated apply ambiguity")
        return original(*args, dry_run=dry_run, **kwargs)

    monkeypatch.setattr(agent_skills, "install_prepared_skill_adapter", fail_apply)
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        AgentSkillConfigureRequest(client=AgentSkillClient.CLAUDE)
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert receipts.values[-1].state is ReceiptState.UNKNOWN
