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
from _launcher.contracts import CommittedEffect, ErrorCode, ReceiptState, WarningCode
from _launcher.contracts import RecoveryRequiredDetails
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
from _launcher.adapter import project_launcher_result


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
    request = BrainInstallRequest((tmp_path / "new").resolve(), "new-brain", client=InstallClient.ALL)

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
    steps = {step.name: step.status for step in result.result.steps}
    assert steps["managed_runtime"] is LifecycleStatus.PLANNED, "skip skips MCP registration only"
    assert steps["mcp_transport"] is LifecycleStatus.NOOP, "the preview lists the step execute reports"
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
        BrainInstallRequest(target, "existing", client=InstallClient.ALL)
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

    result = _invocation(tmp_path).invoke(BrainInstallRequest(target, "partial", client=InstallClient.ALL))

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
                "version": 2,
                "records": [
                    {
                        "schema": "brain.mcp-registration/2",
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


def test_uninstall_removes_every_brain_bootstrap_line_and_keeps_the_prose(tmp_path, monkeypatch):
    from _bootstrap.mcp_state import CLAUDE_MD_BOOTSTRAP_VAULT

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = _vault(tmp_path)
    retired = "ALWAYS DO FIRST: Call MCP `session.start`, else read `.brain-core/index.md` if it exists."
    claude_md = vault / "CLAUDE.md"
    claude_md.write_bytes(f"# Notes\r\nKeep this.\r\n\r\n{retired}\r\n{CLAUDE_MD_BOOTSTRAP_VAULT}\n".encode())

    result = _invocation(tmp_path, vault=vault).invoke(BrainUninstallRequest())

    assert result.result.status is LifecycleStatus.CHANGED
    assert claude_md.read_bytes() == b"# Notes\r\nKeep this.\r\n"


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
    assert result.error.details.recovery_paths
    assert "partially deleted" in result.error.message


def test_uninstall_refuses_unowned_native_transport_before_any_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    vault = _vault(tmp_path)
    config = vault / ".mcp.json"
    config.write_text(json.dumps({"mcpServers": {"brain": {"command": "/legacy/python"}}}))
    before = config.read_bytes()
    result = _invocation(tmp_path, vault=vault).invoke(BrainUninstallRequest())
    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert config.read_bytes() == before
    assert (vault / ".brain-core").is_dir()


def test_upgrade_projects_dry_run_and_closed_policies(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    calls = []
    followup = {
        "id": "configure_managed_approvals",
        "reason": "managed_approvals_available",
        "message": "Optional: inspect managed approvals before choosing configuration.",
        "command": ["brain", "approvals", "inspect", "--json"],
    }

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
            "followups": [followup],
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
    projection = project_launcher_result(result)
    assert projection.structured_content["result"]["followups"] == [followup]
    assert followup["message"] in projection.concise_text
    assert "Run: brain approvals inspect --json" in projection.concise_text


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


def test_upgrade_fails_closed_when_external_rollback_is_not_proven(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    recovery = str((tmp_path / "machine" / "old-cli.backup").resolve())
    receipts = _Receipts()
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
                        "message": "CLI rollback could not be verified",
                        "rollback_verified": True,
                        "recovery_paths": [recovery],
                        "cutover_commit": {
                            "status": "error",
                            "external_rollback_verified": None,
                            "recovery_paths": [recovery],
                        },
                    }
                )
            },
        ),
    )

    result = _invocation(
        tmp_path,
        vault=vault,
        receipts=receipts,
    ).invoke(BrainUpgradeRequest())

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.error.details.recovery_paths == (recovery,)
    assert receipts.values[-1].recovery_paths == (recovery,)
    projected = project_launcher_result(result)
    assert projected.structured_content["error"]["details"][
        "recovery_paths"
    ] == [recovery]


def test_verified_upgrade_rollback_with_residual_staging_is_known_partial(
    tmp_path,
    monkeypatch,
):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    recovery = str((tmp_path / "machine" / ".cli.stage").resolve())
    receipts = _Receipts()
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
                        "message": "Upgrade rolled back; staging cleanup failed.",
                        "rollback_verified": True,
                        "recovery_paths": [recovery],
                        "cutover_commit": {
                            "status": "error",
                            "external_rollback_verified": True,
                            "recovery_paths": [recovery],
                        },
                    }
                )
            },
        ),
    )

    result = _invocation(
        tmp_path,
        vault=vault,
        receipts=receipts,
    ).invoke(BrainUpgradeRequest())

    assert result.status == "partial"
    assert result.error.details.recovery_paths == (recovery,)
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"recovery:{recovery}",
    )
    assert receipts.values[-1].state is ReceiptState.KNOWN_PARTIAL
    assert receipts.values[-1].recovery_paths == (recovery,)
    projected = project_launcher_result(result)
    assert projected.structured_content["error"]["details"][
        "recovery_paths"
    ] == [recovery]


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
    assert lifecycle._reconciliation_recovery_paths(result) == (recovery,)


def test_upgrade_partial_carries_typed_cli_cleanup_recovery_paths(
    tmp_path, monkeypatch
):
    import _distribution

    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    recovery = (tmp_path / "prefix" / "old.backup").resolve()
    installed = _distribution.InstalledDistribution(
        (tmp_path / "bin" / "brain").resolve(),
        (tmp_path / "lib" / "brain-cli" / _distribution.source_versions(REPO_ROOT).cli_version).resolve(),
        _distribution.source_versions(REPO_ROOT).cli_version,
        CORE_VERSION,
        "sha256:abc",
        (recovery,),
    )
    monkeypatch.setattr(_distribution, "install_from_source", lambda *_args: installed)

    def fake_upgrade(*_args, **kwargs):
        return {
            "status": "ok",
            "old_version": "0.54.41",
            "new_version": CORE_VERSION,
            "files_added": [],
            "files_modified": ["VERSION"],
            "files_removed": [],
            "cutover_commit": kwargs["commit_callback"]({}),
        }

    monkeypatch.setattr(
        lifecycle,
        "_load_upgrade",
        lambda _core: type("Upgrade", (), {"upgrade": staticmethod(fake_upgrade)}),
    )

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "partial"
    assert isinstance(result.error.details, RecoveryRequiredDetails)
    assert result.error.details.recovery_paths == (str(recovery),)
    projected = project_launcher_result(result)
    assert projected.structured_content["error"]["details"][
        "recovery_paths"
    ] == [str(recovery)]


def test_a_skip_install_whose_runtime_fails_reports_core_notes(tmp_path, monkeypatch):
    """On the launcher route (approvals present), the runtime remedy reaches the person."""
    import install

    target = (tmp_path / "skip").resolve()
    raw = {"status": "partial", "steps": [
        {"name": "vault_scaffold", "status": "changed", "message": "Created Brain vault scaffold.", "path": str(target)},
        {"name": "managed_runtime", "status": "error", "message": "Could not provision managed runtime: no 3.12"},
        {"name": "mcp_transport", "status": "noop", "message": "MCP registration skipped."},
    ], "notes": ["Vault scaffold is present, but the managed runtime is not: run brain runtime repair."]}
    monkeypatch.setattr(install, "install_vault_action", lambda *_args, **_kwargs: raw)

    result = _invocation(tmp_path).invoke(BrainInstallRequest(target, "skip", mcp_scope=InstallMcpScope.SKIP))

    assert result.status == "partial", result
    assert "no 3.12" in result.error.message and "brain runtime repair" in result.error.message


def test_a_clean_install_carries_core_notes_as_follow_ups(tmp_path, monkeypatch):
    import install

    target = (tmp_path / "skip").resolve()
    raw = {"status": "ok", "steps": [
        {"name": "vault_scaffold", "status": "changed", "message": "Created Brain vault scaffold.", "path": str(target)},
    ], "notes": ["Register MCP later with brain mcp configure."]}
    monkeypatch.setattr(install, "install_vault_action", lambda *_args, **_kwargs: raw)

    result = _invocation(tmp_path).invoke(BrainInstallRequest(target, "skip", mcp_scope=InstallMcpScope.SKIP))

    assert result.status == "ok", result
    assert [warning.message for warning in result.warnings] == ["Register MCP later with brain mcp configure."]


def _fake_upgrader(monkeypatch, result_factory):
    calls = []

    def fake_upgrade(*args, **kwargs):
        calls.append((args, kwargs))
        return result_factory(kwargs)

    monkeypatch.setattr(
        lifecycle,
        "_load_upgrade",
        lambda _core: type("Upgrade", (), {"upgrade": staticmethod(fake_upgrade)}),
    )
    return calls


def test_upgrade_content_ahead_refusal_is_a_no_effect_conflict(tmp_path, monkeypatch):
    """A source older than the recorded content is refused like the preflight's own downgrade refusal (DD-084)."""
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    message = (
        "Upgrade refused — this source is 0.54.41 but the Brain's content is at 0.60.0 "
        "(the migration ledger records 0.60.0 above this source). Migrations only run forward."
    )
    receipts = _Receipts()
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "error",
        "old_version": "0.54.41",
        "new_version": CORE_VERSION,
        "reason": "content_ahead",
        "rollback_verified": True,
        "message": message,
    })

    result = _invocation(tmp_path, vault=vault, receipts=receipts).invoke(BrainUpgradeRequest(force=True))

    assert result.status == "error"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert result.error.message == message
    assert receipts.values[-1].state is ReceiptState.NONE


def test_upgrade_downgrade_is_refused_by_preflight_before_the_upgrader_loads(tmp_path, monkeypatch):
    vault = _vault(tmp_path, version="9.9.9")
    _register(monkeypatch, tmp_path, vault)
    calls = _fake_upgrader(monkeypatch, lambda _kwargs: {"status": "ok"})

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest(force=True))

    assert result.error.code is ErrorCode.CONFLICT
    assert "newer than this CLI distribution" in result.error.message
    assert calls == []


def test_upgrade_force_on_a_same_version_vault_projects_no_migrations(tmp_path, monkeypatch):
    vault = _vault(tmp_path, version=CORE_VERSION)
    _register(monkeypatch, tmp_path, vault)
    calls = _fake_upgrader(monkeypatch, lambda kwargs: {
        "status": "ok",
        "old_version": CORE_VERSION,
        "new_version": CORE_VERSION,
        "files_added": [],
        "files_modified": [],
        "files_removed": [],
        "router_compile": {"outcome": "ok"},
        "cutover_commit": kwargs["commit_callback"]({}),
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest(force=True))

    assert result.status == "ok"
    assert result.result.migrations == ()
    assert calls[0][1]["force"] is True


def _partial_after_commit(extra):
    def factory(kwargs):
        return {
            "status": "partial",
            "old_version": "0.54.41",
            "new_version": CORE_VERSION,
            "files_added": [],
            "files_modified": ["scripts/upgrade.py"],
            "files_removed": [],
            "cutover_commit": kwargs["commit_callback"]({}),
            **extra,
        }
    return factory


def test_upgrade_version_commit_failure_is_partial_with_a_rerun_next_action(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    _fake_upgrader(monkeypatch, _partial_after_commit({
        "version_commit": {"outcome": "error", "message": "could not write .brain-core/VERSION: disk full"},
    }))

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.error.next_action.instruction.startswith("Rerun the same upgrade")
    assert result.committed_effects[0].subject == f"upgrade:{vault}"
    projected = project_launcher_result(result)
    assert "Rerun the same upgrade" in projected.structured_content["error"]["next_action"]["instruction"]


def test_upgrade_router_compile_failure_is_partial_with_refresh_router_next_action(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    _fake_upgrader(monkeypatch, _partial_after_commit({
        "router_compile": {"outcome": "error", "message": "Router recompilation failed after the commit: boom"},
    }))

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "partial"
    assert "runtime.refresh-router" in result.error.next_action.instruction


def test_upgrade_completion_projects_version_commit_and_router_compile_steps():
    result = {
        "version_commit": {"outcome": "error", "message": "could not write VERSION"},
        "router_compile": {"outcome": "error", "message": "router boom"},
    }

    steps = {step.name: step for step in lifecycle._reconciliation_steps(result)}

    assert lifecycle._reconciliation_failed(result) is True
    assert steps["version_commit"].status is LifecycleStatus.CHANGED
    assert steps["version_commit"].message == "could not write VERSION"
    assert steps["router_compile"].status is LifecycleStatus.CHANGED
    assert lifecycle._reconciliation_failed({"router_compile": {"outcome": "ok"}}) is False


def test_upgrade_version_unreadable_refusal_is_a_no_effect_conflict(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    message = f"Upgrade refused — {vault}/.brain-core/VERSION holds '01.0.0', not a version of the form X.Y.Z"
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "error",
        "old_version": "01.0.0",
        "new_version": CORE_VERSION,
        "reason": "version_unreadable",
        "rollback_verified": True,
        "message": message,
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert result.error.message == message


def test_upgrade_ledger_unreadable_refusal_is_a_no_effect_conflict(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    message = f"Upgrade refused — the migration ledger cannot be read ({vault}/.brain/local/migrations.json: ...)"
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "error",
        "old_version": "0.54.41",
        "new_version": CORE_VERSION,
        "reason": "ledger_unreadable",
        "rollback_verified": True,
        "message": message,
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert result.error.message == message


def _ok_result(kwargs, **extra):
    return {
        "status": "ok",
        "old_version": "0.54.41",
        "new_version": CORE_VERSION,
        "files_added": [],
        "files_modified": ["scripts/upgrade.py"],
        "files_removed": [],
        "router_compile": {"outcome": "ok"},
        "cutover_commit": kwargs["commit_callback"]({}),
        **extra,
    }


def test_upgrade_warnings_reach_the_launcher_result_on_ok(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    _fake_upgrader(monkeypatch, lambda kwargs: _ok_result(kwargs, warnings=[
        {"stage": "version_guard", "code": "core_mismatch", "message": "The installed Brain Core differs; re-applying it."},
        {"stage": "upgrade_start", "code": "interrupted_previous_upgrade", "message": "A previous upgrade was interrupted."},
        {"stage": "post_compile_snapshot_seed", "code": "post_compile_snapshot_seed_unavailable", "message": "no router"},
    ]))

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "ok"
    assert [(w.code, w.message) for w in result.warnings] == [
        (WarningCode.FOLLOW_UP_REQUIRED, "core_mismatch: The installed Brain Core differs; re-applying it."),
        (WarningCode.FOLLOW_UP_REQUIRED, "interrupted_previous_upgrade: A previous upgrade was interrupted."),
        (WarningCode.DEGRADED_CAPABILITY, "post_compile_snapshot_seed_unavailable: no router"),
    ]
    projected = project_launcher_result(result)
    assert projected.structured_content["warnings"][0]["code"] == "follow_up_required"


def test_upgrade_warnings_reach_the_launcher_result_on_noop_and_partial(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    interrupted = {"code": "interrupted_previous_upgrade", "message": "interrupted during dependency_sync"}
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "skipped",
        "old_version": CORE_VERSION,
        "new_version": CORE_VERSION,
        "message": f"Already at {CORE_VERSION}.",
        "warnings": [interrupted],
    })
    skipped = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())
    assert skipped.result.status is LifecycleStatus.NOOP
    assert [w.code for w in skipped.warnings] == [WarningCode.FOLLOW_UP_REQUIRED]

    not_durable = {"code": "version_commit_not_durable", "message": "VERSION is written but not fsynced"}
    _fake_upgrader(monkeypatch, _partial_after_commit({
        "router_compile": {"outcome": "error", "message": "boom"},
        "warnings": [not_durable],
    }))
    partial = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())
    assert partial.status == "partial"
    assert [(w.code, w.message) for w in partial.warnings] == [
        (WarningCode.DEGRADED_CAPABILITY, "version_commit_not_durable: VERSION is written but not fsynced"),
    ]


def test_upgrade_force_on_a_same_version_vault_runs_the_real_core_and_no_migration(tmp_path, monkeypatch, fake_home):
    """Through the launcher and the real distribution core: force re-applies, migrations are seeded, none run."""
    import shutil

    from test_upgrade import _make_minimal_upgrade_vault

    vault = _make_minimal_upgrade_vault(tmp_path, version=CORE_VERSION).resolve()
    shutil.rmtree(vault / ".brain-core")
    shutil.copytree(
        REPO_ROOT / "src" / "brain-core", vault / ".brain-core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    _register(monkeypatch, tmp_path, vault)
    (tmp_path / "bin").mkdir()

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest(
        force=True, definition_sync=UpgradePolicy.DISABLE, dependency_sync=UpgradePolicy.DISABLE,
    ))

    assert result.status == "ok", getattr(result, "error", None)
    assert result.result.status is LifecycleStatus.CHANGED
    assert result.result.old_version == result.result.new_version == CORE_VERSION
    assert result.result.migrations == ()
    assert (vault / ".brain-core" / "VERSION").read_text().strip() == CORE_VERSION
    ledger = json.loads((vault / ".brain" / "local" / "migrations.json").read_text())["migrations"]
    assert ledger, "every migration at or below VERSION is seeded"
    assert {entry["status"] for entry in ledger.values()} == {"backfilled"}
    assert {entry["recorded_from"] for entry in ledger.values()} == {f"installed-version:{CORE_VERSION}"}


def test_upgrade_journal_unreadable_refusal_is_a_no_effect_conflict(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    message = "Upgrade refused — the rollback journal of an interrupted upgrade cannot be read (...)"
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "error",
        "old_version": "0.54.41",
        "new_version": CORE_VERSION,
        "reason": "journal_unreadable",
        "rollback_verified": True,
        "message": message,
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert result.error.message == message


_RECOVERED = {"stage": "upgrade_start", "code": "recovered_interrupted_upgrade", "message": "A previous upgrade was rolled back from its journal."}
_RECOVERY = {"action": "restored", "journal": "/state/brain/upgrade-journals/abc", "restored_paths": 3, "preserved_paths": 0, "recovery_directory": None}


def test_upgrade_recovery_warning_requires_follow_up_and_the_recovery_is_an_effect(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    _fake_upgrader(monkeypatch, lambda kwargs: _ok_result(kwargs, warnings=[_RECOVERED], recovery=_RECOVERY))

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "ok"
    assert [(w.code, w.message) for w in result.warnings] == [
        (WarningCode.FOLLOW_UP_REQUIRED, "recovered_interrupted_upgrade: A previous upgrade was rolled back from its journal."),
    ]
    assert result.committed_effects[0] == CommittedEffect(BrainUpgradeRequest.COMMAND_ID, "upgrade-recovery:/state/brain/upgrade-journals/abc")
    assert len(result.committed_effects) == 4


def test_a_refusal_after_a_recovery_is_partial_with_the_recovery_as_its_effect(tmp_path, monkeypatch):
    """A refused run that restored a journal did something; it is never reported as a no-effect error."""
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    message = "Upgrade refused — this source is 0.9.0 but the Brain's content is at 1.0.0."
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "error", "old_version": "1.0.0", "new_version": "0.9.0", "reason": "content_ahead",
        "rollback_verified": True, "message": message, "warnings": [_RECOVERED], "recovery": _RECOVERY,
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT and result.error.message == message
    assert result.committed_effects == (CommittedEffect(BrainUpgradeRequest.COMMAND_ID, "upgrade-recovery:/state/brain/upgrade-journals/abc"),)
    assert [w.code for w in result.warnings] == [WarningCode.FOLLOW_UP_REQUIRED]


def test_a_skip_after_a_recovery_carries_the_recovery_effect(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "skipped", "old_version": CORE_VERSION, "new_version": CORE_VERSION,
        "message": f"Already at {CORE_VERSION}.", "warnings": [_RECOVERED], "recovery": _RECOVERY,
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "ok" and result.result.status is LifecycleStatus.NOOP
    assert result.committed_effects == (CommittedEffect(BrainUpgradeRequest.COMMAND_ID, "upgrade-recovery:/state/brain/upgrade-journals/abc"),)


def test_a_next_run_restore_that_does_not_verify_is_an_unknown_outcome(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _register(monkeypatch, tmp_path, vault)
    journal = "/state/brain/upgrade-journals/abc"
    _fake_upgrader(monkeypatch, lambda _kwargs: {
        "status": "error", "old_version": "1.0.0", "new_version": CORE_VERSION,
        "message": "Upgrade stopped — the interrupted upgrade 1.0.0 → 2.0.0 could not be rolled back.",
        "rollback_verified": False, "recovery_paths": [journal, "/vault/.brain/notes.txt"],
        "rollback": {"vault_state": "unverified", "errors": ["x"], "recovery_paths": [journal]},
    })

    result = _invocation(tmp_path, vault=vault).invoke(BrainUpgradeRequest())

    assert result.status == "error" and result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.error.details.recovery_paths == ("/state/brain/upgrade-journals/abc", "/vault/.brain/notes.txt")
