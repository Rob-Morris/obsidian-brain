"""Tests for repair.py and the shared repair runtime."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import _lifecycle_common as lifecycle_common
import _semantic.config as semantic_config
import _semantic.model as semantic_model
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


def _register_project_client(vault: Path, client: str) -> dict:
    server_config = repair_runtime._expected_project_server_config(vault)
    if client == "claude":
        has_claude_cli = repair_runtime.init._has_claude_cli
        repair_runtime.init._has_claude_cli = lambda: False
        try:
            record = repair_runtime.init.register_claude(vault, server_config, "project", vault)
        finally:
            repair_runtime.init._has_claude_cli = has_claude_cli
    else:
        record = repair_runtime.init.register_codex(server_config, "project", vault)
    repair_runtime.init.record_init_target(vault, record)
    return server_config


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

    def test_bootstrap_invariant_error_is_wrapped_in_structured_envelope(self, repair_vault, monkeypatch, capsys):
        def boom(*_args, **_kwargs):
            raise AssertionError("Created vault-local .venv is not Python 3.12+")

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

        monkeypatch.setattr(lifecycle_common, "probe_python", fake_probe)

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

    def test_repair_main_rejects_corrupt_bootstrap_summary(self, repair_vault, monkeypatch):
        monkeypatch.setenv(repair.MANAGED_RUNTIME_ENV, "1")
        monkeypatch.setenv(repair.BOOTSTRAP_SUMMARY_ENV, "{not-json")
        monkeypatch.setattr(repair, "find_vault_root", lambda _vault: str(repair_vault))

        with pytest.raises(RuntimeError, match="bootstrap summary"):
            repair.main(["router", "--vault", str(repair_vault)])


class TestRepairScopes:
    def test_mcp_repair_is_noop_when_no_project_state_is_present(self, repair_vault, monkeypatch):
        monkeypatch.setattr(repair_runtime.init, "claude_project_followup_notes", lambda _target: [])

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert not (repair_vault / ".mcp.json").exists()
        assert not (repair_vault / ".codex" / "config.toml").exists()
        assert not (repair_vault / "CLAUDE.md").exists()
        assert not (repair_vault / ".claude" / "settings.local.json").exists()
        assert not (repair_vault / ".brain" / "local" / "init-state.json").exists()

    @pytest.mark.parametrize("client", ["claude", "codex"])
    def test_mcp_repair_is_noop_for_healthy_single_client_install(self, repair_vault, monkeypatch, client):
        monkeypatch.setattr(repair_runtime.init, "claude_project_followup_notes", lambda _target: [])
        _register_project_client(repair_vault, client)

        result = repair_runtime.repair_mcp(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert all(step["status"] == "noop" for step in result["steps"])

    def test_mcp_repair_repairs_only_recorded_claude_project_state(self, repair_vault, monkeypatch):
        monkeypatch.setattr(repair_runtime.init, "claude_project_followup_notes", lambda _target: [])
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

    def test_mcp_repair_propagates_programmer_errors(self, repair_vault, monkeypatch):
        monkeypatch.setattr(
            repair_runtime,
            "inspect_mcp",
            lambda _vault: {
                "server_config": {},
                "claude": {"present": False, "healthy": False},
                "codex": {"present": True, "healthy": False},
            },
        )
        monkeypatch.setattr(
            repair_runtime.init,
            "register_codex",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("programmer bug")),
        )

        with pytest.raises(TypeError, match="programmer bug"):
            repair_runtime.repair_mcp(repair_vault, dry_run=False)

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

    def test_semantic_repair_is_noop_when_not_configured(self, repair_vault):
        result = repair_runtime.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert result["steps"][-1]["name"] == "semantic_config"

    def test_semantic_repair_surfaces_config_load_errors(self, repair_vault, monkeypatch):
        monkeypatch.setattr(
            repair_runtime._semantic_config,
            "load_config_checked",
            lambda _vault: (_ for _ in ()).throw(
                repair_runtime._semantic_config.SemanticConfigLoadError(
                    "semantic config is unreadable"
                )
            ),
        )

        result = repair_runtime.repair_semantic(repair_vault, dry_run=False)

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
            repair_runtime,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (True, False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "sync_runtime_packages",
            lambda _python: pytest.fail("runtime sync should not run when dependencies are already available"),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: pytest.fail("asset refresh should not run when sidecars are already present"),
        )

        result = repair_runtime.repair_semantic(repair_vault, dry_run=False)

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

        monkeypatch.setattr(repair_runtime, "probe_python", fake_probe)
        monkeypatch.setattr(repair_runtime._semantic_provision, "probe_python", fake_probe)
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault, manifest_missing=True),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "sync_runtime_packages",
            lambda _python: calls.__setitem__("sync", calls["sync"] + 1),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=True, manifest_changed=True),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: calls.__setitem__("refresh", calls["refresh"] + 1) or ["semantic assets refreshed"],
        )

        result = repair_runtime.repair_semantic(repair_vault, dry_run=False)

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

        monkeypatch.setattr(repair_runtime, "probe_python", fake_probe)
        monkeypatch.setattr(repair_runtime._semantic_provision, "probe_python", fake_probe)
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (True, False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "sync_runtime_packages",
            lambda _python: calls.__setitem__("sync", calls["sync"] + 1),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: pytest.fail("asset refresh should not run when sidecars are already present"),
        )

        result = repair_runtime.repair_semantic(repair_vault, dry_run=False)

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
            repair_runtime,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: (_ for _ in ()).throw(ValueError("boom")),
        )

        result = repair_runtime.repair_semantic(repair_vault, dry_run=False)

        assert result["status"] == "partial"
        assert result["steps"][-2]["name"] == "semantic_assets"
        assert "boom" in result["steps"][-2]["message"]

    def test_semantic_repair_dry_run_uses_shared_planned_step_shapes(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=False)

        monkeypatch.setattr(
            repair_runtime,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": False, "missing": list(modules)},
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )

        result = repair_runtime.repair_semantic(repair_vault, dry_run=True)

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

    def test_semantic_repair_propagates_programmer_errors_from_asset_refresh(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=True)

        monkeypatch.setattr(
            repair_runtime,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "inspect_model_state",
            lambda _vault: _model_state(repair_vault),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (False, False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision.semantic_model,
            "provision_semantic_model",
            lambda _vault: _model_outcome(repair_vault, downloaded=False, manifest_changed=False),
        )
        monkeypatch.setattr(
            repair_runtime._semantic_provision,
            "refresh_semantic_assets",
            lambda _vault: (_ for _ in ()).throw(TypeError("programmer bug")),
        )

        with pytest.raises(TypeError, match="programmer bug"):
            repair_runtime.repair_semantic(repair_vault, dry_run=False)

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
        _register_project_client(repair_vault, "claude")
        (repair_vault / ".mcp.json").unlink()

        result = check.run_checks(str(repair_vault), _wiki_router())

        hit = next(f for f in result["findings"] if f["check"] == "mcp_registration")
        assert hit["repair"]["scope"] == "mcp"
        assert "repair.py mcp" in hit["repair"]["command"]

    @pytest.mark.parametrize("client", ["claude", "codex"])
    def test_valid_single_client_project_install_does_not_report_mcp_drift(self, repair_vault, client):
        _register_project_client(repair_vault, client)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_bootstrap_only_scaffold_does_not_report_mcp_drift(self, repair_vault):
        (repair_vault / "CLAUDE.md").write_text(f"{repair_runtime.init.CLAUDE_MD_BOOTSTRAP_VAULT}\n")

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

        monkeypatch.setattr(repair_runtime, "inspect_mcp", explode)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_bootstrap_only_state_still_skips_mcp_inspection(self, repair_vault, monkeypatch):
        (repair_vault / "CLAUDE.md").write_text(f"{repair_runtime.init.CLAUDE_MD_BOOTSTRAP_VAULT}\n")
        settings_path = repair_vault / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"theme": "dark"}))

        def explode(_vault):
            raise AssertionError("inspect_mcp must not run for bootstrap-only state")

        monkeypatch.setattr(repair_runtime, "inspect_mcp", explode)

        result = check.run_checks(str(repair_vault), _wiki_router())

        assert not any(f["check"] == "mcp_registration" for f in result["findings"])

    def test_semantic_drift_adds_semantic_repair_guidance(self, repair_vault, monkeypatch):
        semantic_config.set_semantic_flags(repair_vault, retrieval=True)
        semantic_config.set_semantic_engine_installed(repair_vault, installed=False)

        monkeypatch.setattr(
            repair_runtime,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": False, "missing": list(modules)},
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
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
            repair_runtime,
            "probe_python",
            lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
        )
        monkeypatch.setattr(
            repair_runtime._semantic_model,
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
            repair_runtime._semantic_model,
            "verify_local_model_load",
            lambda state: state,
        )
        monkeypatch.setattr(
            repair_runtime._semantic_runtime,
            "embeddings_sidecars_match_manifest",
            lambda _vault, _manifest: (sidecars_present, sidecars_present and meta_payload["model_revision"] != semantic_model.SHIPPED_MODEL_REVISION),
        )

        result = check.run_checks(str(repair_vault), _wiki_router())

        semantic_hits = [f["check"] for f in result["findings"] if f["check"].startswith("semantic:")]
        assert expected_check in semantic_hits
