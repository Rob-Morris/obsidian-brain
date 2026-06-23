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


def test_build_repair_command_quotes_spaced_vault_path_on_win32(monkeypatch, tmp_path):
    vault = tmp_path / "Brain Vault"
    vault.mkdir()
    monkeypatch.setattr(repair_common, "find_launcher_python", lambda: r"C:\Program Files\Python312\python.exe")
    monkeypatch.setattr(_shell.sys, "platform", "win32")

    command = repair_common.build_repair_command(vault, "runtime")

    assert '"C:\\Program Files\\Python312\\python.exe"' in command
    assert f'"{vault.resolve()}"' in command


class TestBootstrapSummary:
    def test_runtime_error_is_wrapped_in_structured_envelope(self, repair_vault, monkeypatch, capsys):
        monkeypatch.setattr(
            repair,
            "handoff_current_script_to_managed_runtime",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("Created central managed runtime is not Python 3.12+")
            ),
        )

        exit_code = repair.main(["router", "--vault", str(repair_vault), "--json"])

        assert exit_code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "error"
        assert payload["steps"][0]["name"] == "managed_runtime"
        assert payload["steps"][0]["status"] == "error"
        assert "Python 3.12+" in payload["steps"][0]["message"]

    def test_dry_run_returns_planned_result_when_runtime_is_not_ready(
        self, repair_vault, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            repair,
            "preview_managed_runtime",
            lambda *_args, **_kwargs: {
                "status": "planned",
                "managed_runtime_ready": False,
                "managed_python": "/managed/python",
                "steps": [
                    {"name": "managed_runtime", "status": "planned", "message": "Would create runtime."},
                    {"name": "managed_dependencies", "status": "planned", "message": "Would sync dependencies."},
                ],
            },
        )

        exit_code = repair.main(["router", "--vault", str(repair_vault), "--dry-run", "--json"])

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "planned"
        assert payload["steps"][-1]["name"] == "router"
        assert payload["steps"][-1]["status"] == "planned"

    def test_dry_run_propagates_bootstrap_preview_errors(self, repair_vault, monkeypatch, capsys):
        monkeypatch.setattr(
            repair,
            "preview_managed_runtime",
            lambda *_args, **_kwargs: {
                "status": "error",
                "managed_runtime_ready": False,
                "managed_python": "/managed/python",
                "steps": [
                    {"name": "managed_runtime", "status": "error", "message": "preview failed"},
                ],
            },
        )

        exit_code = repair.main(["router", "--vault", str(repair_vault), "--dry-run", "--json"])

        assert exit_code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "error"
        assert payload["steps"] == [
            {"name": "managed_runtime", "status": "error", "message": "preview failed"},
        ]

    def test_main_uses_bootstrap_steps_from_handoff_summary(self, repair_vault, monkeypatch):
        bootstrap_steps = [{"name": "managed_runtime", "status": "noop", "message": "ready"}]
        captured = {}

        monkeypatch.setattr(
            repair,
            "handoff_current_script_to_managed_runtime",
            lambda *_args, **_kwargs: {
                "managed_runtime_ready": True,
                "managed_python": sys.executable,
                "steps": bootstrap_steps,
            },
        )

        def fake_run_scope(scope, vault_root, *, dry_run, bootstrap_steps):
            captured["scope"] = scope
            captured["vault_root"] = vault_root
            captured["dry_run"] = dry_run
            captured["bootstrap_steps"] = bootstrap_steps
            return {
                "scope": scope,
                "vault_root": str(vault_root),
                "managed_python": sys.executable,
                "status": "noop",
                "steps": list(bootstrap_steps),
            }

        monkeypatch.setattr(repair_runtime, "run_scope", fake_run_scope)

        exit_code = repair.main(["router", "--vault", str(repair_vault)])

        assert exit_code == 0
        assert captured["scope"] == "router"
        assert captured["vault_root"] == repair_vault
        assert captured["dry_run"] is False
        assert captured["bootstrap_steps"] == bootstrap_steps

    def test_dry_run_reexecs_before_running_scope_when_preview_is_ready(self, repair_vault, monkeypatch):
        bootstrap_steps = [{"name": "managed_runtime", "status": "noop", "message": "ready"}]
        captured = {}

        monkeypatch.setattr(
            repair,
            "preview_managed_runtime",
            lambda *_args, **_kwargs: {
                "managed_runtime_ready": True,
                "managed_python": "/managed/python",
                "steps": bootstrap_steps,
            },
        )
        monkeypatch.setattr(repair, "current_process_in_managed_runtime", lambda _vault: False)

        def fake_exec(*, managed_python, script_path, forwarded_args, summary):
            captured["managed_python"] = managed_python
            captured["script_path"] = script_path
            captured["forwarded_args"] = forwarded_args
            captured["summary"] = summary
            raise RuntimeError("reexec")

        monkeypatch.setattr(repair, "exec_managed_runtime", fake_exec)

        with pytest.raises(RuntimeError, match="reexec"):
            repair.main(["router", "--vault", str(repair_vault), "--dry-run"])

        assert captured["managed_python"] == "/managed/python"
        assert captured["forwarded_args"] == ["router", "--vault", str(repair_vault), "--dry-run"]
        assert captured["summary"]["steps"] == bootstrap_steps


class TestRepairScopes:
    def test_runtime_verification_is_noop_when_runtime_is_healthy(self, repair_vault, monkeypatch):
        runtime_python = repair_vault / ".brain" / "managed-runtime" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("")
        monkeypatch.setattr(bootstrap_diagnostics, "resolve_vault_venv_python", lambda _vault: runtime_python)
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )

        result = repair_runtime.verify_runtime_post_bootstrap(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert result["steps"][-1]["name"] == "runtime"
        assert result["steps"][-1]["status"] == "noop"

    def test_runtime_verification_reports_error_when_runtime_is_missing(self, repair_vault, monkeypatch):
        missing_python = repair_vault / ".brain" / "missing-runtime" / "bin" / "python"
        monkeypatch.setattr(bootstrap_diagnostics, "resolve_vault_venv_python", lambda _vault: missing_python)

        result = repair_runtime.verify_runtime_post_bootstrap(repair_vault, dry_run=False)

        assert result["status"] == "error"
        assert result["steps"][-1] == {
            "name": "runtime",
            "status": "error",
            "message": "Central managed runtime is missing for this vault.",
        }

    def test_inspect_runtime_reports_unusable_when_probe_is_incompatible(self, repair_vault, monkeypatch):
        runtime_python = repair_vault / ".brain" / "venv" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("")
        monkeypatch.setattr(bootstrap_diagnostics, "resolve_vault_venv_python", lambda _vault: runtime_python)
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": False, "ok": False, "missing": list(modules)},
        )

        state = bootstrap_diagnostics.inspect_runtime(repair_vault)

        assert state["healthy"] is False
        assert state["issues"] == [repair_runtime.ISSUE_RUNTIME_UNUSABLE]

    def test_inspect_runtime_reports_probe_error_context(self, repair_vault, monkeypatch):
        runtime_python = repair_vault / ".brain" / "venv" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("")
        monkeypatch.setattr(bootstrap_diagnostics, "resolve_vault_venv_python", lambda _vault: runtime_python)
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "probe_python",
            lambda _python_path, *, modules=(): {
                "compatible": False,
                "ok": False,
                "missing": [],
                "probe_error": "permission denied",
            },
        )

        state = bootstrap_diagnostics.inspect_runtime(repair_vault)

        assert state["healthy"] is False
        assert state["issues"] == [repair_runtime.ISSUE_RUNTIME_UNUSABLE]
        assert state["probe_error"] == "permission denied"
        assert "permission denied" in state["message"]

    def test_inspect_runtime_reports_missing_baseline_packages(self, repair_vault, monkeypatch):
        runtime_python = repair_vault / ".brain" / "venv" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("")
        monkeypatch.setattr(bootstrap_diagnostics, "resolve_vault_venv_python", lambda _vault: runtime_python)
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": False, "missing": ["mcp"]},
        )

        state = bootstrap_diagnostics.inspect_runtime(repair_vault)

        assert state["healthy"] is False
        assert state["issues"] == [repair_runtime.ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING]
        assert state["missing_modules"] == ["mcp"]

    def test_inspect_runtime_preserves_venv_symlink_boundary(self, repair_vault, monkeypatch):
        runtime_python = repair_vault / ".brain" / "venv" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.symlink_to(sys.executable)
        captured = {}
        monkeypatch.setattr(bootstrap_diagnostics, "resolve_vault_venv_python", lambda _vault: runtime_python)
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "probe_python",
            lambda python_path, *, modules=(): captured.setdefault(
                "probe",
                {"compatible": True, "ok": True, "missing": [], "python_path": python_path},
            ),
        )

        state = bootstrap_diagnostics.inspect_runtime(repair_vault)

        assert state["healthy"] is True
        assert state["python"] == str(runtime_python)
        assert captured["probe"]["python_path"] == str(runtime_python)

    def test_mcp_repair_returns_error_when_runtime_is_unhealthy(self, repair_vault, monkeypatch):
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

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "error"
        assert result["steps"][-1] == {
            "name": "runtime",
            "status": "error",
            "message": "Central managed runtime is missing for this vault.",
        }

    def test_mcp_repair_is_noop_when_no_project_state_is_present(self, repair_vault, monkeypatch):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert not (repair_vault / ".mcp.json").exists()
        assert not (repair_vault / ".codex" / "config.toml").exists()
        assert not (repair_vault / "CLAUDE.md").exists()
        assert not (repair_vault / ".claude" / "settings.local.json").exists()
        assert not (repair_vault / ".brain" / "local" / "init-state.json").exists()

    @pytest.mark.parametrize("client", ["claude", "codex"])
    def test_mcp_repair_is_noop_for_healthy_single_client_install(self, repair_vault, monkeypatch, client):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])
        _register_project_client(repair_vault, client)

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert all(step["status"] == "noop" for step in result["steps"])

    def test_mcp_repair_repairs_only_recorded_claude_project_state(self, repair_vault, monkeypatch):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])
        _register_project_client(repair_vault, "claude")
        (repair_vault / ".mcp.json").unlink()

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert (repair_vault / ".mcp.json").is_file()
        assert (repair_vault / "CLAUDE.md").is_file()
        assert (repair_vault / ".claude" / "settings.local.json").is_file()
        assert not (repair_vault / ".codex" / "config.toml").exists()
        state = json.loads((repair_vault / ".brain" / "local" / "init-state.json").read_text())
        assert [record["client"] for record in state["records"]] == ["claude"]

    def test_mcp_repair_replaces_legacy_claude_session_hook(self, repair_vault, monkeypatch):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(repair_runtime.mcp_transport, "claude_project_followup_notes", lambda _target: [])
        server_config = _register_project_client(repair_vault, "claude")
        legacy_command = _write_legacy_session_hook(repair_vault)

        before = bootstrap_diagnostics.inspect_mcp(repair_vault)
        assert before["claude"]["hook_ok"] is False
        assert before["claude"]["hook_state"]["stale_count"] == 1

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        settings = json.loads((repair_vault / ".claude" / "settings.local.json").read_text())
        commands = [
            hook["command"]
            for entry in settings["hooks"]["SessionStart"]
            for hook in entry["hooks"]
        ]
        expected = repair_runtime.mcp_transport.build_session_hook_command(
            repair_vault,
            repair_vault,
            python_path=server_config["command"],
        )
        assert commands == [expected]
        assert legacy_command not in commands
        after = bootstrap_diagnostics.inspect_mcp(repair_vault)
        assert after["claude"]["hook_ok"] is True

    def test_mcp_repair_propagates_programmer_errors(self, repair_vault, monkeypatch):
        _mock_healthy_runtime(monkeypatch)
        monkeypatch.setattr(
            bootstrap_diagnostics,
            "inspect_mcp",
            lambda _vault: {
                "server_config": {},
                "claude": {"present": False, "healthy": False},
                "codex": {"present": True, "healthy": False},
            },
        )
        monkeypatch.setattr(
            repair_runtime.mcp_transport,
            "register_codex",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("programmer bug")),
        )

        with pytest.raises(TypeError, match="programmer bug"):
            repair_runtime.repair_mcp(repair_vault, dry_run=False)

    def test_router_repair_builds_compiled_router(self, repair_vault):
        result = repair_runtime.repair_router(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert result["steps"][-1]["message"] == (
            "Rebuilt the compiled router (missing) and cleared semantic embeddings sidecars."
        )
        assert (repair_vault / ".brain" / "local" / "compiled-router.json").is_file()

    def test_router_repair_uses_shared_cache_detector(self, repair_vault, monkeypatch):
        monkeypatch.setattr(
            repair_runtime,
            "inspect_router_cache",
            lambda _vault: CacheState(
                stale=True,
                reason="source-newer-than-router",
                path=".brain/local/compiled-router.json",
            ),
        )

        result = repair_runtime.repair_router(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert result["steps"][-1]["message"] == (
            "Would rebuild the compiled router (source-newer-than-router) and clear semantic embeddings sidecars."
        )

    def test_lexical_repair_builds_retrieval_index(self, repair_vault):
        result = repair_runtime.repair_lexical(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert (repair_vault / ".brain" / "local" / "retrieval-index.json").is_file()

    def test_lexical_repair_dry_run_plans_rebuild(self, repair_vault):
        result = repair_runtime.repair_lexical(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert result["steps"] == [
            {
                "name": "lexical",
                "status": "planned",
                "message": "Would rebuild the lexical retrieval index (missing).",
            }
        ]

    def test_lexical_repair_uses_shared_cache_detector(self, repair_vault, monkeypatch):
        monkeypatch.setattr(
            repair_runtime,
            "inspect_lexical_cache",
            lambda _vault: CacheState(
                stale=True,
                reason="version-drift",
                path=".brain/local/retrieval-index.json",
            ),
        )

        result = repair_runtime.repair_lexical(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert result["steps"][-1]["message"] == (
            "Would rebuild the lexical retrieval index (version-drift)."
        )

    def test_registry_repair_normalises_bare_string_entries(self, repair_vault):
        registry_path = repair_vault / ".brain" / "local" / "workspaces.json"
        registry_path.write_text(json.dumps({"workspaces": {"ext": "/tmp/ext"}}))

        result = repair_runtime.repair_registry(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        repaired = json.loads(registry_path.read_text())
        assert repaired == {"workspaces": {"ext": {"path": "/tmp/ext"}}}

    def test_frontmatter_repair_merges_nested_frontmatter_blocks(self, repair_vault, monkeypatch):
        bad = repair_vault / "Wiki" / "Duplicate Frontmatter.md"
        bad.write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "key: duplicate-frontmatter\n"
            "status: active\n"
            "---\n\n"
            "---\n"
            "status: shaping\n"
            "tags:\n"
            "  - bug\n"
            "---\n"
            "# Body\n"
        )

        repair_time = "2026-05-19T12:34:56+10:00"
        monkeypatch.setattr(frontmatter_repairs, "now_iso", lambda: repair_time)
        result = repair_runtime.repair_frontmatter(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert result["steps"][-1]["name"] == "frontmatter"
        assert result["steps"][-1]["status"] == "changed"
        assert "Duplicate Frontmatter.md" in "\n".join(result["notes"])
        assert bad.read_text() == (
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "  - bug\n"
            "key: duplicate-frontmatter\n"
            "status: active\n"
            "modified: 2026-05-19T12:34:56+10:00\n"
            "---\n"
            "# Body\n"
        )

    def test_frontmatter_repair_dry_run_does_not_mutate_files(self, repair_vault):
        bad = repair_vault / "Wiki" / "Dry Run.md"
        original = (
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "key: dry-run\n"
            "modified: 2026-05-01T09:00:00+10:00\n"
            "---\n"
            "---\n"
            "status: active\n"
            "modified: 2026-04-01T09:00:00+10:00\n"
            "---\n"
            "# Body\n"
        )
        bad.write_text(original)

        result = repair_runtime.repair_frontmatter(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert result["steps"][-1]["name"] == "frontmatter"
        assert result["steps"][-1]["status"] == "planned"
        assert bad.read_text() == original

    def test_frontmatter_repair_uses_shared_detection_preflight(self, repair_vault, monkeypatch):
        calls = []

        def fake_detect(vault_root):
            calls.append(Path(vault_root))
            return []

        monkeypatch.setattr(
            frontmatter_repairs,
            "detect_duplicate_frontmatter_documents",
            fake_detect,
        )

        result = repair_runtime.repair_frontmatter(repair_vault, dry_run=True)

        assert result["status"] == "noop"
        assert calls == [repair_vault]

    def test_semantic_repair_is_noop_when_not_configured(self, repair_vault):
        result = semantic_repairs.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert result["steps"][-1]["name"] == "semantic_config"

    def test_inspect_semantic_skips_local_model_load_when_runtime_dependencies_are_missing(
        self,
        repair_vault,
        monkeypatch,
    ):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=True)

        monkeypatch.setattr(
            semantic_repairs,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": False, "missing": list(modules)},
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda _state: pytest.fail("local model load should be skipped when runtime dependencies are missing"),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (True, False),
        )

        state = semantic_repairs.inspect_semantic(repair_vault)

        assert state["dependencies_ok"] is False
        assert semantic_repairs.ISSUE_SEMANTIC_RUNTIME_DEPENDENCIES_MISSING in state["issues"]
        assert state["model_state"].load_error is None

    def test_semantic_repair_surfaces_config_load_errors(self, repair_vault, monkeypatch):
        monkeypatch.setattr(
            semantic_repairs.semantic_config,
            "load_config_checked",
            lambda _vault: (_ for _ in ()).throw(
                semantic_repairs.semantic_config.SemanticConfigLoadError(
                    "semantic config is unreadable"
                )
            ),
        )

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "error"
        assert result["steps"][-1] == {
            "name": "semantic_config",
            "status": "error",
            "message": "semantic config is unreadable",
        }

    def test_semantic_repair_marks_runtime_when_only_marker_is_missing(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=False)

        monkeypatch.setattr(
            semantic_repairs,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (True, False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "sync_runtime_packages",
            lambda _python: pytest.fail("runtime sync should not run when dependencies are already available"),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: pytest.fail("asset refresh should not run when sidecars are already present"),
        )

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert [step["name"] for step in result["steps"]] == [
            "semantic_runtime",
            "semantic_model",
            "semantic_runtime_marker",
        ]
        cfg = semantic_config.load_config_checked(repair_vault)
        assert semantic_config.semantic_retrieval_enabled(repair_vault, config=cfg) is True
        assert semantic_config.semantic_engine_installed(repair_vault, config=cfg) is True

    def test_semantic_repair_syncs_runtime_and_rebuilds_sidecars_when_unhealthy(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=False)

        calls = {"probe": 0, "sync": 0, "refresh": 0}

        def fake_probe(_python_path, *, modules=()):
            calls["probe"] += 1
            if calls["probe"] == 1:
                return {"compatible": True, "ok": False, "missing": list(modules)}
            return {"compatible": True, "ok": True, "missing": []}

        monkeypatch.setattr(semantic_repairs, "probe_python", fake_probe)
        monkeypatch.setattr(semantic_repairs.semantic_provision, "probe_python", fake_probe)
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault, manifest_missing=True),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "sync_runtime_packages",
            lambda _python: calls.__setitem__("sync", calls["sync"] + 1),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=True, manifest_changed=True),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: calls.__setitem__("refresh", calls["refresh"] + 1) or ["semantic assets refreshed"],
        )

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert calls["sync"] == 1
        assert calls["refresh"] == 1
        assert result["notes"] == ["semantic assets refreshed"]
        assert [step["name"] for step in result["steps"]] == [
            "semantic_runtime",
            "semantic_model",
            "semantic_assets",
            "semantic_runtime_marker",
        ]

    def test_semantic_repair_skips_asset_refresh_when_sidecars_are_present(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=False)

        calls = {"probe": 0, "sync": 0}

        def fake_probe(_python_path, *, modules=()):
            calls["probe"] += 1
            if calls["probe"] == 1:
                return {"compatible": True, "ok": False, "missing": list(modules)}
            return {"compatible": True, "ok": True, "missing": []}

        monkeypatch.setattr(semantic_repairs, "probe_python", fake_probe)
        monkeypatch.setattr(semantic_repairs.semantic_provision, "probe_python", fake_probe)
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (True, False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "sync_runtime_packages",
            lambda _python: calls.__setitem__("sync", calls["sync"] + 1),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: pytest.fail("asset refresh should not run when sidecars are already present"),
        )

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert calls["sync"] == 1
        assert [step["name"] for step in result["steps"]] == [
            "semantic_runtime",
            "semantic_model",
            "semantic_runtime_marker",
        ]

    def test_semantic_repair_returns_structured_error_when_asset_refresh_fails(self, repair_vault, monkeypatch):
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
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: (_ for _ in ()).throw(
                semantic_repairs.semantic_provision.SemanticRuntimeUnavailableError("boom")
            ),
        )

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "partial"
        assert result["steps"][-2]["name"] == "semantic_assets"
        assert "boom" in result["steps"][-2]["message"]

    def test_semantic_repair_dry_run_uses_shared_planned_step_shapes(self, repair_vault, monkeypatch):
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

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert result["steps"] == [
            {
                "name": "semantic_runtime",
                "status": "planned",
                "message": "Would provision or re-sync the pinned semantic runtime dependencies for this vault.",
            },
            {
                "name": "semantic_model",
                "status": "planned",
                "message": "Would provision or update the pinned local semantic model snapshot for this vault.",
            },
            {
                "name": "semantic_assets",
                "status": "planned",
                "message": "Would rebuild the compiled router, retrieval index, and semantic embeddings sidecars.",
            },
            {
                "name": "semantic_runtime_marker",
                "status": "planned",
                "message": "Would mark the local semantic runtime as provisioned for this vault once semantic provisioning completes successfully.",
            },
        ]

    def test_semantic_repair_dry_run_skips_marker_when_already_set(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=True)

        monkeypatch.setattr(
            semantic_repairs,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        model_state = _model_state(repair_vault)
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "inspect_model_state",
            lambda _vault: model_state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )

        result = semantic_repairs.repair_semantic(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert [step["name"] for step in result["steps"]] == ["semantic_assets"]

    def test_semantic_repair_propagates_programmer_errors_from_asset_refresh(self, repair_vault, monkeypatch):
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
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            semantic_repairs.semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: (_ for _ in ()).throw(TypeError("programmer bug")),
        )

        with pytest.raises(TypeError, match="programmer bug"):
            semantic_repairs.repair_semantic(repair_vault, dry_run=False)

    def test_router_repair_propagates_programmer_errors_from_session_refresh(self, repair_vault, monkeypatch):
        monkeypatch.setattr(repair_runtime.compile_router, "refresh_session_markdown", lambda *_args: (_ for _ in ()).throw(TypeError("bad refresh")))

        with pytest.raises(TypeError, match="bad refresh"):
            repair_runtime.repair_router(repair_vault, dry_run=False)

