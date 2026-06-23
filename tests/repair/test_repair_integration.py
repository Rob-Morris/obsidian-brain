"""Tests for repair.py and the shared repair runtime."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _bootstrap.diagnostics as bootstrap_diagnostics
import _bootstrap.mcp_state as bootstrap_mcp_state
import _bootstrap.runtime as bootstrap_runtime
from _common import _shell
import _lifecycle.frontmatter_repairs as frontmatter_repairs
from _lifecycle.derived_cache_state import CacheState
import _lifecycle.semantic_repairs as semantic_repairs
import _semantic.config as semantic_config
import _semantic.model as semantic_model
import _repair_common as repair_common
import _repair_runtime as repair_runtime
import check
import migrate_to_0_48_2
import repair
from brain_test_support import make_router, write_md

from _repair_helpers import (
    _mock_healthy_runtime,
    _model_outcome,
    _model_state,
    _register_project_client,
    _wiki_router,
    _write_legacy_session_hook,
)


@pytest.mark.slow
class TestRepairSubprocessIntegration:
    def test_router_repair_runs_from_bare_launcher_without_mcp_deps(self, repair_vault, tmp_path):
        launcher_venv = tmp_path / "launcher-venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(launcher_venv)],
            check=True,
            timeout=60,
        )
        launcher_python = launcher_venv / "bin" / "python"

        # If router repair incorrectly tries to install MCP dependencies, the
        # poisoned requirements + no-index env should force a failure.
        (repair_vault / ".brain-core" / "brain_mcp" / "requirements.txt").write_text(
            "definitely-not-a-real-package-for-brain-repair==0.0\n"
        )

        env = os.environ.copy()
        env["PIP_NO_INDEX"] = "1"
        result = subprocess.run(
            [
                str(launcher_python),
                str(Path(repair.__file__).resolve()),
                "router",
                "--vault",
                str(repair_vault),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert (repair_vault / ".brain" / "local" / "compiled-router.json").is_file()
        assert "This repair scope does not require additional managed runtime dependencies." in result.stdout
        assert "Rebuilt the compiled router" in result.stdout

    def test_lexical_repair_runs_from_bare_launcher_without_mcp_deps(self, repair_vault, tmp_path):
        launcher_venv = tmp_path / "launcher-venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(launcher_venv)],
            check=True,
            timeout=60,
        )
        launcher_python = launcher_venv / "bin" / "python"

        (repair_vault / ".brain-core" / "brain_mcp" / "requirements.txt").write_text(
            "definitely-not-a-real-package-for-brain-repair==0.0\n"
        )

        env = os.environ.copy()
        env["PIP_NO_INDEX"] = "1"
        result = subprocess.run(
            [
                str(launcher_python),
                str(Path(repair.__file__).resolve()),
                "lexical",
                "--vault",
                str(repair_vault),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert (repair_vault / ".brain" / "local" / "retrieval-index.json").is_file()
        assert "This repair scope does not require additional managed runtime dependencies." in result.stdout
        assert "Rebuilt the lexical retrieval index" in result.stdout


class TestCheckRepairHints:
    def test_missing_router_uses_detected_launcher_in_repair_guidance(self, tmp_path, monkeypatch):
        (tmp_path / ".brain-core").mkdir()
        (tmp_path / ".brain-core" / "VERSION").write_text("0.32.5\n")
        monkeypatch.setattr(repair_common, "find_launcher_python", lambda: "/opt/homebrew/bin/python3.13")

        result = check.run_checks(str(tmp_path))

        finding = result["findings"][0]
        assert finding["repair"]["command"].startswith("/opt/homebrew/bin/python3.13 ")

    def test_missing_router_includes_router_repair_guidance(self, tmp_path):
        (tmp_path / ".brain-core").mkdir()
        (tmp_path / ".brain-core" / "VERSION").write_text("0.32.5\n")

        result = check.run_checks(str(tmp_path))

        finding = result["findings"][0]
        assert finding["repair"]["scope"] == "router"
        assert "repair.py router" in finding["repair"]["command"]

    def test_registry_drift_adds_registry_repair_guidance(self, repair_vault):
        registry_path = repair_vault / ".brain" / "local" / "workspaces.json"
        registry_path.write_text(json.dumps({"workspaces": {"ext": "/tmp/ext"}}))

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "workspace_registry")
        assert hit["repair"]["scope"] == "registry"
        assert "repair.py registry" in hit["repair"]["command"]

    def test_duplicate_frontmatter_adds_frontmatter_repair_guidance(self, repair_vault):
        (repair_vault / "Wiki" / "Broken.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "key: broken\n"
            "---\n"
            "---\n"
            "status: shaping\n"
            "---\n"
            "# Broken\n"
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "duplicate_frontmatter")
        assert hit["repair"]["scope"] == "frontmatter"
        assert "repair.py frontmatter" in hit["repair"]["command"]

    def test_legacy_index_scope_errors_with_rename_hint(self, repair_vault, capsys):
        with pytest.raises(SystemExit) as exc:
            repair.parse_args(["index", "--vault", str(repair_vault)])

        assert exc.value.code == 2
        assert "renamed to 'lexical'" in capsys.readouterr().err

    def test_mcp_drift_adds_mcp_repair_guidance(self, repair_vault):
        _register_project_client(repair_vault, "claude")
        (repair_vault / ".mcp.json").unlink()

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "mcp_registration")
        assert hit["repair"]["scope"] == "mcp"
        assert "repair.py mcp" in hit["repair"]["command"]

    def test_mcp_python_mismatch_adds_specific_repair_guidance(self, repair_vault):
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        stale = dict(expected)
        stale["command"] = "/usr/bin/python3.12"
        stale["args"] = ["-m", "brain_mcp.proxy", "/usr/bin/python3.12", "brain_mcp.server"]
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": stale}}))

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(
            f for f in result["findings"]
            if f["check"] == "mcp_registration:claude_python_mismatch"
        )
        assert hit["file"] == ".mcp.json"
        assert hit["repair"]["scope"] == "mcp"

    def test_mcp_python_check_uses_launch_path_identity(self, repair_vault):
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        equivalent_command = str(Path(expected["command"]).parent / ".." / "bin" / "python")
        stale = dict(expected)
        stale["command"] = equivalent_command
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": stale}}))

        state = bootstrap_diagnostics.inspect_mcp(repair_vault)

        assert state["claude"]["command_ok"] is True
        assert state["claude"]["config_ok"] is False

    def test_codex_mcp_python_mismatch_adds_specific_repair_guidance(self, repair_vault):
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        stale = dict(expected)
        stale["command"] = "/usr/bin/python3.12"
        stale["args"] = ["-m", "brain_mcp.proxy", "/usr/bin/python3.12", "brain_mcp.server"]
        repair_runtime.mcp_transport.write_codex_config(
            stale,
            repair_vault / ".codex" / "config.toml",
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(
            f for f in result["findings"]
            if f["check"] == "mcp_registration:codex_python_mismatch"
        )
        assert hit["file"] == ".codex/config.toml"
        assert hit["repair"]["scope"] == "mcp"

    def test_specific_mcp_finding_does_not_suppress_other_client_generic_drift(self, repair_vault):
        _register_project_client(repair_vault, "claude")
        (repair_vault / "CLAUDE.md").write_text("missing bootstrap\n")
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        stale = dict(expected)
        stale["command"] = "/usr/bin/python3.12"
        stale["args"] = ["-m", "brain_mcp.proxy", "/usr/bin/python3.12", "brain_mcp.server"]
        repair_runtime.mcp_transport.write_codex_config(
            stale,
            repair_vault / ".codex" / "config.toml",
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        checks = [f["check"] for f in result["findings"]]
        generic = [
            f for f in result["findings"]
            if f["check"] == "mcp_registration"
        ]
        assert "mcp_registration:codex_python_mismatch" in checks
        assert any("Claude Brain MCP project registration state" in f["message"] for f in generic)

    def test_claude_session_hook_drift_adds_specific_repair_guidance(self, repair_vault):
        _register_project_client(repair_vault, "claude")
        _write_legacy_session_hook(repair_vault)

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(
            f for f in result["findings"]
            if f["check"] == "mcp_registration:claude_session_hook_missing"
        )
        assert hit["file"] == ".claude/settings.local.json"
        assert hit["repair"]["scope"] == "mcp"

    def test_0_48_2_migration_converges_stale_mcp_state(self, repair_vault, monkeypatch):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        stale = dict(expected)
        stale["command"] = "/usr/bin/python3.12"
        stale["args"] = ["-m", "brain_mcp.proxy", "/usr/bin/python3.12", "brain_mcp.server"]
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": stale}}))
        _write_legacy_session_hook(repair_vault)
        repair_runtime.mcp_transport.record_init_target(repair_vault, {
            "client": "claude",
            "scope": "project",
            "target_path": str(repair_vault),
            "config_path": str(repair_vault / ".mcp.json"),
            "server_name": "brain",
            "server_config": stale,
            "hook_path": str(repair_vault / ".claude" / "settings.local.json"),
            "hook_command": "legacy",
            "method": "test",
        })

        result = migrate_to_0_48_2.migrate(str(repair_vault))

        assert result["status"] == "ok"
        findings = bootstrap_diagnostics.collect_mcp_check_findings(repair_vault)
        assert findings == []

    def test_0_48_2_migration_noops_without_mcp_state(self, repair_vault, monkeypatch):
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])

        result = migrate_to_0_48_2.migrate(str(repair_vault))

        assert result["status"] == "noop"
        assert [step["name"] for step in result["steps"]] == ["claude_project", "codex_project"]
        assert not (repair_vault / ".mcp.json").exists()
        assert not (repair_vault / ".codex" / "config.toml").exists()
        assert not (repair_vault / ".claude" / "settings.local.json").exists()
        assert not (repair_vault / ".brain" / "local" / "init-state.json").exists()

    def test_0_48_2_migration_is_idempotent_after_convergence(self, repair_vault, monkeypatch):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        stale = dict(expected)
        stale["command"] = "/usr/bin/python3.12"
        stale["args"] = ["-m", "brain_mcp.proxy", "/usr/bin/python3.12", "brain_mcp.server"]
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": stale}}))
        _write_legacy_session_hook(repair_vault)
        repair_runtime.mcp_transport.record_init_target(repair_vault, {
            "client": "claude",
            "scope": "project",
            "target_path": str(repair_vault),
            "config_path": str(repair_vault / ".mcp.json"),
            "server_name": "brain",
            "server_config": stale,
            "hook_path": str(repair_vault / ".claude" / "settings.local.json"),
            "hook_command": "legacy",
            "method": "test",
        })

        first = migrate_to_0_48_2.migrate(str(repair_vault))
        second = migrate_to_0_48_2.migrate(str(repair_vault))

        assert first["status"] == "ok"
        assert second["status"] == "noop"
        settings = json.loads((repair_vault / ".claude" / "settings.local.json").read_text())
        hook_commands = [
            command
            for command in bootstrap_diagnostics._session_hook_commands(settings)
            if bootstrap_mcp_state.is_session_hook_command(command, repair_vault, repair_vault)
        ]
        assert len(hook_commands) == 1
        assert bootstrap_diagnostics.collect_mcp_check_findings(repair_vault) == []

    def test_0_48_2_migration_reports_error_on_partial_repair(self, repair_vault, monkeypatch):
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": {}}}))
        monkeypatch.setattr(
            migrate_to_0_48_2,
            "_repair_claude",
            lambda _vault, _server_config, _state, _dry_run: {
                "name": "claude_project",
                "status": "changed",
                "message": "ok",
            },
        )
        monkeypatch.setattr(
            migrate_to_0_48_2,
            "_repair_codex",
            lambda _vault, _server_config, _state, _dry_run: {
                "name": "codex_project",
                "status": "error",
                "message": "failed",
            },
        )

        result = migrate_to_0_48_2.migrate(str(repair_vault))

        assert result["status"] == "error"

    def test_0_48_2_migration_cli_exits_nonzero_on_repair_error(
        self, repair_vault, monkeypatch, capsys
    ):
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": {}}}))
        monkeypatch.setattr(
            migrate_to_0_48_2,
            "_repair_claude",
            lambda _vault, _server_config, _state, _dry_run: {
                "name": "claude_project",
                "status": "changed",
                "message": "ok",
            },
        )
        monkeypatch.setattr(
            migrate_to_0_48_2,
            "_repair_codex",
            lambda _vault, _server_config, _state, _dry_run: {
                "name": "codex_project",
                "status": "error",
                "message": "failed",
            },
        )

        exit_code = migrate_to_0_48_2.main(["--vault", str(repair_vault)])

        payload = json.loads(capsys.readouterr().out)
        assert exit_code == 2
        assert payload["status"] == "error"

    def test_0_48_2_migration_cli_dry_run_does_not_rewrite_mcp_state(
        self, repair_vault, monkeypatch, capsys
    ):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])
        expected = bootstrap_diagnostics._expected_project_server_config(repair_vault)
        stale = dict(expected)
        stale["command"] = "/usr/bin/python3.12"
        stale["args"] = ["-m", "brain_mcp.proxy", "/usr/bin/python3.12", "brain_mcp.server"]
        mcp_path = repair_vault / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"brain": stale}}, indent=2))
        legacy_command = _write_legacy_session_hook(repair_vault)
        before_mcp = mcp_path.read_text()
        settings_path = repair_vault / ".claude" / "settings.local.json"
        before_settings = settings_path.read_text()

        exit_code = migrate_to_0_48_2.main(["--vault", str(repair_vault), "--dry-run"])

        payload = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert payload["status"] == "planned"
        assert mcp_path.read_text() == before_mcp
        assert settings_path.read_text() == before_settings
        assert legacy_command in settings_path.read_text()

    def test_runtime_drift_adds_runtime_repair_guidance(self, repair_vault, monkeypatch):
        _register_project_client(repair_vault, "claude")
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "inspect_runtime",
            lambda _vault: {
                "healthy": False,
                "python": str(repair_vault / ".brain" / "missing-runtime" / "bin" / "python"),
                "issues": [repair_runtime.ISSUE_RUNTIME_MISSING],
                "missing_modules": ["mcp"],
                "message": "Central managed runtime is missing for this vault.",
            },
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "runtime:runtime-missing")
        assert hit["repair"]["scope"] == "runtime"
        assert "repair.py runtime" in hit["repair"]["command"]

    def test_runtime_drift_surfaces_probe_error_message_in_check_output(self, repair_vault, monkeypatch):
        _register_project_client(repair_vault, "claude")
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "inspect_runtime",
            lambda _vault: {
                "healthy": False,
                "python": str(repair_vault / ".brain" / "managed-runtime" / "bin" / "python"),
                "issues": [repair_runtime.ISSUE_RUNTIME_UNUSABLE],
                "missing_modules": [],
                "probe_error": "permission denied",
                "message": "Central managed runtime is present but could not be probed: permission denied.",
            },
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "runtime:runtime-unusable")
        assert hit["message"] == "Central managed runtime is present but could not be probed: permission denied."

    @pytest.mark.parametrize("client", ["claude", "codex"])
    def test_valid_single_client_project_install_does_not_report_mcp_drift(self, repair_vault, client):
        _register_project_client(repair_vault, client)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_bootstrap_only_scaffold_does_not_report_mcp_drift(self, repair_vault):
        (repair_vault / "CLAUDE.md").write_text(f"{repair_runtime.mcp_transport.CLAUDE_MD_BOOTSTRAP_VAULT}\n")

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_unrelated_claude_local_settings_do_not_report_mcp_drift(self, repair_vault):
        settings_path = repair_vault / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]
            }
        }))

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_no_mcp_state_skips_mcp_inspection(self, repair_vault, monkeypatch):
        def explode(_vault):
            raise AssertionError("inspect_mcp must not run when no MCP state is present")

        monkeypatch.setattr(bootstrap_diagnostics, "inspect_mcp", explode)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_bootstrap_only_state_still_skips_mcp_inspection(self, repair_vault, monkeypatch):
        (repair_vault / "CLAUDE.md").write_text(f"{repair_runtime.mcp_transport.CLAUDE_MD_BOOTSTRAP_VAULT}\n")
        settings_path = repair_vault / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"theme": "dark"}))

        def explode(_vault):
            raise AssertionError("inspect_mcp must not run for bootstrap-only state")

        monkeypatch.setattr(bootstrap_diagnostics, "inspect_mcp", explode)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_semantic_drift_adds_semantic_repair_guidance(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=False)

        monkeypatch.setattr(
            semantic_repairs,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": False, "missing": list(modules)},
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        semantic_hits = [f for f in result["findings"] if f["check"].startswith("semantic:")]
        assert {f["check"] for f in semantic_hits} == {
            "semantic:runtime-not-provisioned",
            "semantic:runtime-dependencies-missing",
            "semantic:semantic-model-manifest-missing",
            "semantic:semantic-sidecars-missing",
        }
        assert all(hit["repair"]["scope"] == "semantic" for hit in semantic_hits)
        assert all("repair.py semantic" in hit["repair"]["command"] for hit in semantic_hits)

    @pytest.mark.parametrize(
        ("model_state", "sidecars_present", "meta_payload", "expected_check"),
        [
            (
                _model_state(Path("/tmp"), manifest_missing=False, model_path_missing=True),
                True,
                {
                    "model": semantic_model.SHIPPED_MODEL_NAME,
                    "model_revision": semantic_model.SHIPPED_MODEL_REVISION,
                },
                "semantic:semantic-model-path-missing",
            ),
            (
                _model_state(Path("/tmp"), manifest_missing=False, model_revision_mismatch=True),
                True,
                {
                    "model": semantic_model.SHIPPED_MODEL_NAME,
                    "model_revision": semantic_model.SHIPPED_MODEL_REVISION,
                },
                "semantic:semantic-model-revision-mismatch",
            ),
            (
                _model_state(Path("/tmp"), manifest_missing=False, load_error="semantic model manifest is unreadable"),
                True,
                {
                    "model": semantic_model.SHIPPED_MODEL_NAME,
                    "model_revision": semantic_model.SHIPPED_MODEL_REVISION,
                },
                "semantic:semantic-model-load-error",
            ),
            (
                _model_state(Path("/tmp")),
                True,
                {
                    "model": semantic_model.SHIPPED_MODEL_NAME,
                    "model_revision": "different-revision",
                },
                "semantic:semantic-sidecars-outdated",
            ),
        ],
    )
    def test_semantic_drift_reports_specific_model_and_sidecar_findings(
        self,
        repair_vault,
        monkeypatch,
        model_state,
        sidecars_present,
        meta_payload,
        expected_check,
    ):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=True)

        monkeypatch.setattr(
            semantic_repairs,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "inspect_model_state",
            lambda _vault: replace(
                model_state,
                snapshot_path=semantic_model.model_snapshot_path(
                    repair_vault,
                    semantic_model.SHIPPED_MODEL_NAME,
                    semantic_model.SHIPPED_MODEL_REVISION,
                ),
            ),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (sidecars_present, sidecars_present and meta_payload["model_revision"] != semantic_model.SHIPPED_MODEL_REVISION),
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        semantic_hits = [f["check"] for f in result["findings"] if f["check"].startswith("semantic:")]
        assert expected_check in semantic_hits

