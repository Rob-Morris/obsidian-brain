"""Behavioural tests for launcher-owned Brain lifecycle commands."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import stat
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher import lifecycle
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ErrorCode, ReceiptState
from _launcher.invocation import LauncherInvocation
from _launcher.lifecycle import (
    BrainInstallRequest,
    BrainUninstallRequest,
    BrainUpgradeRequest,
    InstallClient,
    InstallMcpScope,
    InstallMode,
    LifecycleStatus,
    UpgradePolicy,
)
from _launcher.owners import LAUNCHER_OWNERS


NOW = datetime.fromisoformat("2026-08-10T08:00:00+10:00")
CORE_VERSION = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"
    available = True


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _vault(tmp_path, version="0.54.41"):
    vault = (tmp_path / "Brain").resolve()
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text(version + "\n")
    (vault / ".brain" / "local").mkdir(parents=True)
    return vault


def _register(monkeypatch, tmp_path, vault):
    config = tmp_path / "config"
    registry = config / "brain" / "vaults"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(f"# brain registry v2\nselected\tlocal\t{vault}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))


def _invocation(
    tmp_path,
    *,
    vault=None,
    distribution=REPO_ROOT,
    dry_run=False,
    receipts=None,
):
    context = LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings((_CallerFilesystem(),)),
        correlation_id="corr-lifecycle",
        invocation_id="inv-lifecycle",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=(tmp_path / "home").resolve(),
        cli_version="2.0.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=vault,
        distribution_root=distribution.resolve() if distribution is not None else None,
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _raw_install(target, *, error=False):
    steps = [
        {"name": "vault_scaffold", "status": "changed", "message": "installed"},
        {"name": "vault_registry", "status": "changed", "message": "registered"},
    ]
    if error:
        steps.append({"name": "managed_runtime", "status": "error", "message": "pip failed"})
    return {"status": "error" if error else "ok", "vault_root": str(target), "steps": steps}


def test_lifecycle_owners_match_launcher_catalogue():
    entries = {
        entry.command_id: entry
        for entry in LAUNCHER_CATALOGUE.entries
        if entry.command_id in {"brain.install", "brain.uninstall", "brain.upgrade"}
    }
    owners = {
        owner.command_id: owner
        for owner in LAUNCHER_OWNERS.entries
        if owner.command_id in entries
    }

    assert tuple(entries) == ("brain.install", "brain.uninstall", "brain.upgrade")
    assert (
        entries["brain.install"].owner_ref
        == owners["brain.install"].owner_ref
        == "_launcher.lifecycle:install"
    )
    assert (
        entries["brain.uninstall"].owner_ref
        == owners["brain.uninstall"].owner_ref
        == "_launcher.lifecycle:uninstall"
    )
    assert (
        entries["brain.upgrade"].owner_ref
        == owners["brain.upgrade"].owner_ref
        == "_launcher.lifecycle:upgrade"
    )
    assert all(entry.required_providers == ("caller_filesystem",) for entry in entries.values())


def test_lifecycle_request_grammar_is_closed(tmp_path):
    target = (tmp_path / "target").resolve()
    invalid = (
        lambda: BrainInstallRequest(target, "Not Valid"),
        lambda: BrainInstallRequest(target, "valid", mcp_scope="project"),
        lambda: BrainInstallRequest(target, "valid", client="all"),
        lambda: BrainUpgradeRequest(force="yes"),
        lambda: BrainUpgradeRequest(definition_sync="auto"),
    )
    for build in invalid:
        try:
            build()
        except ValueError:
            pass
        else:
            raise AssertionError("lifecycle request unexpectedly accepted an open value")


def test_install_requires_trusted_complete_distribution(tmp_path):
    request = BrainInstallRequest((tmp_path / "new").resolve(), "new-brain")

    result = _invocation(tmp_path, distribution=None).invoke(request)

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.effects == "none"


def test_install_dry_run_validates_registry_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = (tmp_path / "new-brain").resolve()

    result = _invocation(tmp_path, dry_run=True).invoke(
        BrainInstallRequest(
            target,
            "new-brain",
            mcp_scope=InstallMcpScope.SKIP,
            client=InstallClient.CLAUDE,
        )
    )

    assert result.result.status is LifecycleStatus.PLANNED
    assert result.result.mode is InstallMode.FRESH
    assert result.result.brain_core_version == CORE_VERSION
    assert result.committed_effects == ()
    assert not target.exists()
    assert not (tmp_path / "config").exists()


def test_install_maps_changed_steps_and_preserves_preinstall_mode(tmp_path, monkeypatch):
    import install

    target = (tmp_path / "existing").resolve()
    target.mkdir()
    (target / "note.md").write_text("preserve\n")
    monkeypatch.setattr(install, "install_vault_action", lambda *_args, **_kwargs: _raw_install(target))
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        BrainInstallRequest(target, "existing")
    )

    assert result.result.status is LifecycleStatus.CHANGED
    assert result.result.mode is InstallMode.EXISTING_VAULT
    assert len(result.committed_effects) == 2
    assert receipts.values[-1].state is ReceiptState.COMMITTED


def test_install_retains_known_partial_steps(tmp_path, monkeypatch):
    import install

    target = (tmp_path / "partial").resolve()
    monkeypatch.setattr(
        install,
        "install_vault_action",
        lambda *_args, **_kwargs: _raw_install(target, error=True),
    )

    result = _invocation(tmp_path).invoke(BrainInstallRequest(target, "partial"))

    assert result.status == "partial"
    assert len(result.committed_effects) == 2
    assert "pip failed" in result.error.message


def test_uninstall_dry_run_preserves_system_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = _vault(tmp_path)
    (vault / ".venv").mkdir()

    result = _invocation(tmp_path, vault=vault, dry_run=True).invoke(
        BrainUninstallRequest()
    )

    assert result.result.status is LifecycleStatus.PLANNED
    assert len(result.result.removed_paths) == 3
    assert (vault / ".brain-core").is_dir()
    assert (vault / ".brain").is_dir()
    assert (vault / ".venv").is_dir()


def test_uninstall_removes_only_system_paths_and_preserves_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = _vault(tmp_path)
    (vault / ".venv").mkdir()
    note = vault / "note.md"
    note.write_text("preserve\n")
    server = {"command": "/managed/python", "args": [], "env": {}}
    config_path = vault / ".mcp.json"
    config_path.write_text(json.dumps({"mcpServers": {"brain": server}}))
    (vault / ".brain" / "local" / "init-state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "client": "claude",
                        "scope": "project",
                        "target_path": str(vault),
                        "config_path": str(config_path),
                        "server_config": server,
                    }
                ],
            }
        )
    )

    result = _invocation(tmp_path, vault=vault).invoke(BrainUninstallRequest())

    assert result.result.status is LifecycleStatus.CHANGED
    assert note.read_text() == "preserve\n"
    assert not (vault / ".brain-core").exists()
    assert not (vault / ".brain").exists()
    assert not (vault / ".venv").exists()
    assert not config_path.exists()
    assert any(effect.subject == f"file:{config_path}" for effect in result.committed_effects)


def test_uninstall_refuses_symlinked_system_state_before_mutation(tmp_path):
    vault = _vault(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (vault / ".brain").rename(vault / ".brain-real")
    (vault / ".brain").symlink_to(external, target_is_directory=True)

    result = _invocation(tmp_path, vault=vault).invoke(BrainUninstallRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert external.is_dir()
    assert (vault / ".brain-core").is_dir()


def test_uninstall_recursive_failure_is_outcome_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = _vault(tmp_path)
    monkeypatch.setattr(lifecycle.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(OSError("busy")))

    result = _invocation(tmp_path, vault=vault).invoke(BrainUninstallRequest())

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False


def test_upgrade_projects_dry_run_and_closed_policies(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    calls = []

    def fake_upgrade(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "status": "ok",
            "old_version": "0.54.41",
            "new_version": CORE_VERSION,
            "files_added": ["one"],
            "files_modified": ["two", "three"],
            "files_removed": [],
            "precompile_patch_migrations_preview": [
                {"version": "0.62.2", "target": "pre_compile_patch"}
            ],
            "migrations_preview": [
                {"version": "0.55.0", "target": "post_compile"}
            ],
        }

    monkeypatch.setattr(
        lifecycle,
        "_load_upgrade",
        lambda _core: type("Upgrade", (), {"upgrade": staticmethod(fake_upgrade)}),
    )
    result = _invocation(tmp_path, vault=vault, dry_run=True).invoke(
        BrainUpgradeRequest(
            definition_sync=UpgradePolicy.DISABLE,
            dependency_sync=UpgradePolicy.ENABLE,
        )
    )

    assert result.result.status is LifecycleStatus.PLANNED
    assert (result.result.files_added, result.result.files_modified) == (1, 2)
    assert result.result.migrations == (
        "0.62.2@pre_compile_patch",
        "0.55.0",
    )
    assert result.committed_effects == ()
    assert calls[0][1]["sync"] is False
    assert calls[0][1]["sync_deps"] is True
    assert calls[0][1]["dry_run"] is True


def test_upgrade_success_receipts_core_and_error_is_unknown(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    cli_binary = tmp_path / "bin" / "brain"
    cli_binary.parent.mkdir()
    cli_binary.write_text("old cli\n")
    cli_binary.chmod(0o755)
    monkeypatch.setattr(
        lifecycle,
        "_load_upgrade",
        lambda _core: type(
            "Upgrade",
            (),
            {
                "upgrade": staticmethod(
                    lambda *_args, **kwargs: {
                        "status": "ok",
                        "old_version": "0.54.41",
                        "new_version": CORE_VERSION,
                        "files_added": [],
                        "files_modified": ["VERSION"],
                        "files_removed": [],
                        "cutover_commit": kwargs["commit_callback"]({}),
                    }
                )
            },
        ),
    )
    success = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())
    assert success.result.status is LifecycleStatus.CHANGED
    assert success.committed_effects[0].subject == f"upgrade:{vault}"
    assert f'BRAIN_INSTALL_REF="v{CORE_VERSION}"' in cli_binary.read_text()
    assert stat.S_IMODE(cli_binary.stat().st_mode) == 0o755
    assert success.committed_effects[1].subject == f"file:{cli_binary}"

    monkeypatch.setattr(
        lifecycle,
        "_load_upgrade",
        lambda _core: type(
            "Upgrade",
            (),
            {
                "upgrade": staticmethod(
                    lambda *_args, **_kwargs: {
                        "status": "error",
                        "message": "rollback uncertain",
                        "rollback_verified": False,
                    }
                )
            },
        ),
    )
    failed = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())
    assert failed.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert failed.effects == "unknown"


def test_upgrade_completion_projects_readiness_failure_and_orphan_follow_up():
    result = {
        "mcp_registration_repair": {
            "outcome": "error",
            "message": "MCP registration repair failed",
        },
        "runtime_readiness": {
            "outcome": "error",
            "message": "warm-up failed",
        },
        "runtime_orphans": {
            "outcome": "follow_up",
            "message": "one orphan is a safe cleanup candidate",
        },
    }

    steps = {
        step.name: step for step in lifecycle._reconciliation_steps(result)
    }

    assert lifecycle._reconciliation_failed(result) is True
    assert steps["mcp_registration"].status is LifecycleStatus.CHANGED
    assert steps["runtime_readiness"].status is LifecycleStatus.CHANGED
    assert steps["runtime_orphans"].status is LifecycleStatus.PLANNED


def test_upgrade_completion_projects_committed_cli_cleanup_recovery():
    recovery = "/machine/lib/brain-cli/.old.backup"
    result = {
        "cutover_commit": {
            "cleanup_recovery_paths": [recovery],
        }
    }

    steps = lifecycle._reconciliation_steps(result)

    assert lifecycle._reconciliation_failed(result) is True
    assert len(steps) == 1
    assert steps[0].name == "cli_backup_cleanup"
    assert recovery in steps[0].message
