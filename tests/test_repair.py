"""Tests for repair.py and the shared repair runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _repair_common as repair_common
import _repair_runtime as repair_runtime
import check
import repair
from conftest import make_router, write_md


@pytest.fixture
def repair_vault(tmp_path):
    """Minimal vault that can exercise repair scopes."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.32.5\n")
    (bc / "session-core.md").write_text("Always:\n- Keep types tidy.\n")
    (bc / "brain_mcp").mkdir()
    (bc / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\npyyaml>=6.0\n")

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


class TestBootstrapSummary:
    def test_plans_runtime_and_dependency_repair_when_managed_runtime_missing(self, repair_vault):
        launcher = sys.executable
        summary = repair._bootstrap_summary(
            repair_vault,
            scope="mcp",
            launcher_python=launcher,
            dry_run=True,
        )

        assert summary["status"] == "planned"
        assert [step["name"] for step in summary["steps"]] == [
            "managed_runtime",
            "managed_dependencies",
        ]
        assert all(step["status"] == "planned" for step in summary["steps"])

    def test_runtime_error_is_wrapped_in_structured_envelope(self, repair_vault, monkeypatch, capsys):
        def boom(*_args, **_kwargs):
            raise RuntimeError("Created vault-local .venv is not Python 3.12+")

        monkeypatch.setattr(repair, "_bootstrap_summary", boom)
        monkeypatch.setattr(repair, "_find_launcher_python", lambda: sys.executable)
        monkeypatch.delenv("BRAIN_REPAIR_MANAGED", raising=False)

        exit_code = repair.main(["router", "--vault", str(repair_vault), "--json"])

        assert exit_code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "error"
        assert payload["steps"][0]["name"] == "managed_runtime"
        assert payload["steps"][0]["status"] == "error"
        assert "Python 3.12+" in payload["steps"][0]["message"]

    def test_non_mcp_scope_does_not_plan_dependency_sync(self, repair_vault, monkeypatch):
        managed_python = repair_vault / ".venv" / "bin" / "python"
        managed_python.parent.mkdir(parents=True)
        managed_python.write_text("")

        def fake_probe(_python_path, *, modules=()):
            if modules:
                return {"compatible": True, "ok": False, "missing": list(modules)}
            return {"compatible": True, "ok": True, "missing": []}

        monkeypatch.setattr(repair, "probe_python", fake_probe)

        summary = repair._bootstrap_summary(
            repair_vault,
            scope="router",
            launcher_python=sys.executable,
            dry_run=False,
        )

        assert summary["status"] == "ready"
        assert summary["steps"][0]["status"] == "noop"
        assert summary["steps"][1]["status"] == "noop"
        assert "does not require additional managed runtime dependencies" in summary["steps"][1]["message"]


class TestRepairScopes:
    def test_mcp_repair_writes_project_configs_and_init_state(self, repair_vault, monkeypatch):
        monkeypatch.setattr(repair_runtime.init, "claude_project_followup_notes", lambda _target: [])

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert (repair_vault / ".mcp.json").is_file()
        assert (repair_vault / ".codex" / "config.toml").is_file()
        assert (repair_vault / "CLAUDE.md").is_file()
        assert (repair_vault / ".claude" / "settings.local.json").is_file()
        state = json.loads((repair_vault / ".brain" / "local" / "init-state.json").read_text())
        assert len(state["records"]) == 2
        project_config = json.loads((repair_vault / ".mcp.json").read_text())
        assert "brain" in project_config["mcpServers"]

    def test_mcp_repair_is_noop_once_project_state_is_healthy(self, repair_vault, monkeypatch):
        monkeypatch.setattr(repair_runtime.init, "claude_project_followup_notes", lambda _target: [])
        repair_runtime.repair_mcp(repair_vault, dry_run=False)

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert all(step["status"] == "noop" for step in result["steps"])

    def test_router_repair_builds_compiled_router(self, repair_vault):
        result = repair_runtime.repair_router(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert (repair_vault / ".brain" / "local" / "compiled-router.json").is_file()

    def test_index_repair_builds_retrieval_index(self, repair_vault):
        result = repair_runtime.repair_index(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert (repair_vault / ".brain" / "local" / "retrieval-index.json").is_file()

    def test_registry_repair_normalises_bare_string_entries(self, repair_vault):
        registry_path = repair_vault / ".brain" / "local" / "workspaces.json"
        registry_path.write_text(json.dumps({"workspaces": {"ext": "/tmp/ext"}}))

        result = repair_runtime.repair_registry(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        repaired = json.loads(registry_path.read_text())
        assert repaired == {"workspaces": {"ext": {"path": "/tmp/ext"}}}


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


class TestCheckRepairHints:
    def test_missing_router_uses_detected_launcher_in_repair_guidance(self, tmp_path, monkeypatch):
        (tmp_path / ".brain-core").mkdir()
        (tmp_path / ".brain-core" / "VERSION").write_text("0.32.5\n")
        monkeypatch.setattr(repair_common, "find_repair_launcher", lambda: "/opt/homebrew/bin/python3.13")

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

    def test_mcp_drift_adds_mcp_repair_guidance(self, repair_vault):
        (repair_vault / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "mcp_registration")
        assert hit["repair"]["scope"] == "mcp"
        assert "repair.py mcp" in hit["repair"]["command"]

    def test_no_mcp_state_skips_mcp_inspection(self, repair_vault, monkeypatch):
        def explode(_vault):
            raise AssertionError("inspect_mcp must not run when no MCP state is present")

        monkeypatch.setattr(repair_runtime, "inspect_mcp", explode)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])
