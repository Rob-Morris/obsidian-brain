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
from conftest import make_router, write_md


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Repair tests resolve the central runtime under `~/.brain/venvs/`.

    Redirect HOME to a per-test directory so we never read or create real
    venvs in the developer's home — and so tests cannot pass via leftover
    state from a previous run.
    """
    fake_home = tmp_path / "_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))


@pytest.fixture
def repair_vault(tmp_path):
    """Minimal vault that can exercise repair scopes."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.32.5\n")
    (bc / "session-core.md").write_text("Always:\n- Keep types tidy.\n")
    (bc / "brain_mcp").mkdir()
    (bc / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")
    # Repair loads the canonical venv path-resolver from the vault to avoid
    # duplicating the rule in repair.py — copy it from source so the fixture
    # mirrors a real vault layout.
    venv_helper_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "brain-core" / "scripts" / "_common" / "_venv.py"
    )
    venv_helper_dst = bc / "scripts" / "_common" / "_venv.py"
    venv_helper_dst.parent.mkdir(parents=True)
    venv_helper_dst.write_text(venv_helper_src.read_text())

    (tmp_path / ".brain" / "local").mkdir(parents=True)
    (tmp_path / "_Config").mkdir()
    (tmp_path / "_Config" / "router.md").write_text("Brain vault.\n\nAlways:\n- Typed folders.\n")
    (tmp_path / "Wiki").mkdir()
    write_md(
        tmp_path / "Wiki" / "Test Page.md",
        {"type": "living/wiki", "tags": ["test"], "key": "test-page"},
        "# Test Page",
    )
    return tmp_path


def test_build_repair_command_quotes_spaced_vault_path_on_win32(monkeypatch, tmp_path):
    vault = tmp_path / "Brain Vault"
    vault.mkdir()
    monkeypatch.setattr(repair_common, "find_launcher_python", lambda: r"C:\Program Files\Python312\python.exe")
    monkeypatch.setattr(_shell.sys, "platform", "win32")

    command = repair_common.build_repair_command(vault, "runtime")

    assert '"C:\\Program Files\\Python312\\python.exe"' in command
    assert f'"{vault.resolve()}"' in command


def _wiki_router():
    artefact = {
        "folder": "Wiki",
        "type": "living/wiki",
        "key": "wiki",
        "classification": "living",
        "configured": True,
        "path": "Wiki",
        "naming": {"pattern": "{name}.md", "folder": "Wiki/"},
        "frontmatter": {
            "type": "living/wiki",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Living/wiki.md",
        "template_file": None,
        "trigger": None,
    }
    return make_router([artefact], meta={"brain_core_version": "0.32.5"})


def _register_project_client(vault: Path, client: str) -> dict:
    server_config = bootstrap_diagnostics._expected_project_server_config(vault)
    if client == "claude":
        has_claude_cli = repair_runtime.mcp_transport._has_claude_cli
        repair_runtime.mcp_transport._has_claude_cli = lambda: False
        try:
            record = repair_runtime.mcp_transport.register_claude(vault, server_config, "project", vault)
        finally:
            repair_runtime.mcp_transport._has_claude_cli = has_claude_cli
    else:
        record = repair_runtime.mcp_transport.register_codex(server_config, "project", vault)
    repair_runtime.mcp_transport.record_init_target(vault, record)
    return server_config


def _write_legacy_session_hook(vault: Path, *, machine_python: str = "/usr/bin/python3.12") -> str:
    legacy_command = (
        "echo 'brain_session called:' "
        f"&& {machine_python} {vault / '.brain-core' / 'scripts' / 'session.py'} "
        f"--vault {vault} --workspace-dir {vault} --json"
    )
    settings_path = vault / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {"type": "command", "command": legacy_command},
                    ]
                }
            ]
        }
    }))
    return legacy_command


def _model_outcome(vault: Path, *, downloaded=False, manifest_changed=False):
    return semantic_model.SemanticModelProvisionOutcome(
        model_name=semantic_model.SHIPPED_MODEL_NAME,
        revision=semantic_model.SHIPPED_MODEL_REVISION,
        local_path=str(
            semantic_model.model_snapshot_path(
                vault,
                semantic_model.SHIPPED_MODEL_NAME,
                semantic_model.SHIPPED_MODEL_REVISION,
            )
        ),
        downloaded=downloaded,
        manifest_changed=manifest_changed,
        notes=(),
    )


def _model_state(
    vault: Path,
    *,
    manifest_missing=False,
    model_path_missing=False,
    model_revision_mismatch=False,
    load_error=None,
):
    manifest = None
    if not manifest_missing:
        manifest = semantic_model.ModelManifest(
            model_name=semantic_model.SHIPPED_MODEL_NAME,
            revision=semantic_model.SHIPPED_MODEL_REVISION,
            provisioned_at="2026-05-06T00:00:00+10:00",
        )
    return semantic_model.ModelState(
        manifest=manifest,
        snapshot_path=semantic_model.model_snapshot_path(
            vault,
            semantic_model.SHIPPED_MODEL_NAME,
            semantic_model.SHIPPED_MODEL_REVISION,
        ),
        manifest_missing=manifest_missing,
        model_path_missing=model_path_missing,
        model_revision_mismatch=model_revision_mismatch,
        load_error=load_error,
    )


def _mock_healthy_runtime(monkeypatch):
    monkeypatch.setattr(
        bootstrap_diagnostics,
        "inspect_runtime",
        lambda _vault: {
            "healthy": True,
            "python": sys.executable,
            "issues": [],
            "missing_modules": [],
            "message": "Central managed runtime is ready for packageful Brain work.",
        },
    )


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
