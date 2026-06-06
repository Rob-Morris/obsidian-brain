"""Tests for scripts/configure.py semantic setup flow."""

from __future__ import annotations

import json
import subprocess
import sys
import pytest

import config as config_module
import configure
import _bootstrap.runtime as bootstrap_runtime
import _lifecycle.retrieval_assets as retrieval_assets
import _semantic.config as semantic_config
import _semantic.model as semantic_model
import _semantic.provision as semantic_provision


def _load_local_config(vault):
    return semantic_config.load_config_checked(vault)


def _make_vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.34.2\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (bc / "brain_mcp").mkdir()
    (bc / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")
    (tmp_path / ".brain" / "local").mkdir(parents=True)
    return tmp_path


def _model_outcome(vault, *, downloaded=False, manifest_changed=False):
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
    )


def test_configure_semantic_enable_sets_flags_before_provisioning(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    calls = {"probe": 0, "sync": 0, "refresh": 0}

    def fake_probe(_python_path, *, modules=()):
        calls["probe"] += 1
        if calls["probe"] == 1:
            return {"compatible": True, "ok": False, "missing": list(modules)}
        return {"compatible": True, "ok": True, "missing": []}

    def fake_sync(_python):
        calls["sync"] += 1
        assert semantic_config.semantic_retrieval_enabled(vault) is True

    def fake_refresh(_vault):
        calls["refresh"] += 1
        assert semantic_config.semantic_retrieval_enabled(vault) is True
        return []

    monkeypatch.setattr(semantic_provision, "probe_python", fake_probe)
    monkeypatch.setattr(semantic_provision, "semantic_runtime_supported_platform", lambda: (True, None))
    monkeypatch.setattr(semantic_provision, "sync_runtime_packages", fake_sync)
    monkeypatch.setattr(
        semantic_provision.semantic_model,
        "provision_semantic_model",
        lambda _vault: _model_outcome(vault, downloaded=True, manifest_changed=True),
    )
    monkeypatch.setattr(semantic_provision, "refresh_semantic_assets", fake_refresh)

    result = configure._configure_semantic_enable(vault, provision=True, bootstrap_steps=[])

    cfg = _load_local_config(vault)
    assert result["status"] == "ok"
    assert calls["sync"] == 1
    assert calls["refresh"] == 1
    assert semantic_config.semantic_retrieval_enabled(vault, config=cfg) is True
    assert semantic_config.semantic_engine_installed(vault, config=cfg) is True


def test_configure_semantic_enable_no_provision_only_writes_flags(tmp_path):
    vault = _make_vault(tmp_path)

    result = configure._configure_semantic_enable(vault, provision=False, bootstrap_steps=[])

    cfg = _load_local_config(vault)
    assert result["status"] == "ok"
    assert semantic_config.semantic_retrieval_enabled(vault, config=cfg) is True
    assert semantic_config.semantic_engine_installed(vault, config=cfg) is False
    assert "--no-provision" in result["notes"][0]


def test_configure_semantic_enable_noop_when_flag_is_already_enabled(tmp_path):
    vault = _make_vault(tmp_path)
    semantic_config.set_semantic_flags(vault, retrieval=True)

    result = configure._configure_semantic_enable(vault, provision=False, bootstrap_steps=[])

    assert result["status"] == "noop"
    assert result["steps"][0]["name"] == "semantic_config"
    assert result["steps"][0]["status"] == "noop"


def test_configure_semantic_enable_unsupported_platform_keeps_flags_enabled(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    semantic_config.set_semantic_engine_installed(vault, installed=True)

    monkeypatch.setattr(
        semantic_provision,
        "semantic_runtime_supported_platform",
        lambda: (False, "semantic runtime is unsupported here"),
    )

    result = configure._configure_semantic_enable(vault, provision=True, bootstrap_steps=[])

    cfg = _load_local_config(vault)
    assert result["status"] == "partial"
    assert semantic_config.semantic_retrieval_enabled(vault, config=cfg) is True
    assert semantic_config.semantic_engine_installed(vault, config=cfg) is False
    assert result["steps"][-1]["name"] == "semantic_runtime"
    assert result["steps"][-1]["status"] == "error"


def test_provision_semantic_runtime_records_asset_error_when_forced_rebuild_fails(
    tmp_path,
    monkeypatch,
):
    vault = _make_vault(tmp_path)

    monkeypatch.setattr(
        semantic_provision,
        "probe_python",
        lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
    )
    monkeypatch.setattr(
        semantic_provision.semantic_model,
        "provision_semantic_model",
        lambda _vault: _model_outcome(vault, downloaded=False, manifest_changed=False),
    )
    monkeypatch.setattr(
        semantic_provision,
        "refresh_semantic_assets",
        lambda _vault: (_ for _ in ()).throw(
            semantic_provision.SemanticRuntimeUnavailableError(
                "semantic runtime dependencies are unavailable: numpy is not installed",
                operation="building semantic embeddings",
            )
        ),
    )

    outcome = semantic_provision.provision_semantic_runtime(
        vault,
        python_executable="/managed/python",
        runtime_ok=True,
        refresh_assets=True,
    )

    assert outcome.assets_changed is False
    assert outcome.assets_error == (
        "Semantic runtime is ready, but retrieval asset refresh failed because semantic runtime dependencies are unavailable: "
        "semantic runtime dependencies are unavailable: numpy is not installed while building semantic embeddings"
    )
    assert outcome.marker_installed is False


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            semantic_provision.CompiledRouterUnavailableError(
                "compiled router refresh failed: bad naming rule",
                operation="refreshing retrieval assets",
            ),
            "Semantic runtime is ready, but retrieval asset refresh failed because the compiled router is unavailable: "
            "compiled router refresh failed: bad naming rule while refreshing retrieval assets",
        ),
        (
            semantic_provision.RetrievalPersistenceError(
                ".brain/local/compiled-router.json",
                "persisting compiled router",
                ValueError("symlink refused"),
            ),
            "Semantic runtime is ready, but retrieval asset refresh failed because derived retrieval state could not be persisted: "
            "failed to persist retrieval output '.brain/local/compiled-router.json' while persisting compiled router: symlink refused",
        ),
        (
            semantic_provision.SemanticRuntimeUnavailableError(
                "semantic runtime dependencies are unavailable: numpy is not installed",
                operation="building semantic embeddings",
            ),
            "Semantic runtime is ready, but retrieval asset refresh failed because semantic runtime dependencies are unavailable: "
            "semantic runtime dependencies are unavailable: numpy is not installed while building semantic embeddings",
        ),
        (
            semantic_model.SemanticModelMissingError("semantic model snapshot is missing"),
            "Semantic runtime is ready, but retrieval asset refresh failed because the local semantic model is unavailable or unusable: "
            "semantic model snapshot is missing",
        ),
    ],
)
def test_provision_semantic_runtime_formats_typed_asset_errors(
    tmp_path,
    monkeypatch,
    error,
    expected_message,
):
    vault = _make_vault(tmp_path)

    def raise_refresh(_vault, exc):
        raise exc

    monkeypatch.setattr(
        semantic_provision,
        "probe_python",
        lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
    )
    monkeypatch.setattr(
        semantic_provision.semantic_model,
        "provision_semantic_model",
        lambda _vault: _model_outcome(vault, downloaded=False, manifest_changed=False),
    )
    monkeypatch.setattr(
        semantic_provision,
        "refresh_semantic_assets",
        lambda _vault: raise_refresh(_vault, error),
    )

    outcome = semantic_provision.provision_semantic_runtime(
        vault,
        python_executable="/managed/python",
        runtime_ok=True,
        refresh_assets=True,
    )

    assert outcome.assets_error == expected_message
    assert outcome.marker_installed is False


def test_probe_python_raises_on_zero_exit_invalid_json(monkeypatch):
    def fake_run(_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["/fake/python", "-c", "probe"],
            returncode=0,
            stdout="not-json\n",
            stderr="",
        )

    monkeypatch.setattr(bootstrap_runtime.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="produced invalid JSON"):
        bootstrap_runtime.probe_python("/fake/python")


def test_configure_main_uses_bootstrap_steps_from_handoff_summary(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    bootstrap_steps = [{"name": "managed_runtime", "status": "noop", "message": "ready"}]
    captured = {}

    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))
    monkeypatch.setattr(
        configure,
        "handoff_current_script_to_managed_runtime",
        lambda *_args, **_kwargs: {
            "managed_runtime_ready": True,
            "managed_python": sys.executable,
            "steps": bootstrap_steps,
        },
    )

    def fake_configure(vault_root, *, provision, bootstrap_steps):
        captured["vault_root"] = vault_root
        captured["provision"] = provision
        captured["bootstrap_steps"] = bootstrap_steps
        return {
            "action": "semantic_enable",
            "vault_root": str(vault_root),
            "managed_python": "/managed/python",
            "status": "ok",
            "steps": list(bootstrap_steps),
        }

    monkeypatch.setattr(configure, "_configure_semantic_enable", fake_configure)

    exit_code = configure.main(["semantic", "--enable", "--vault", str(vault), "--json"])

    assert exit_code == 0
    assert captured["provision"] is True
    assert captured["bootstrap_steps"] == bootstrap_steps
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["steps"] == bootstrap_steps


def test_configure_main_human_output_uses_exit_code_for_partial(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)

    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))
    monkeypatch.setattr(
        configure,
        "handoff_current_script_to_managed_runtime",
        lambda *_args, **_kwargs: {
            "managed_runtime_ready": True,
            "managed_python": sys.executable,
            "steps": [{"name": "managed_runtime", "status": "noop", "message": "ready"}],
        },
    )
    monkeypatch.setattr(
        configure,
        "_configure_semantic_enable",
        lambda *_args, **_kwargs: {
            "action": "semantic_enable",
            "vault_root": str(vault),
            "managed_python": "/managed/python",
            "status": "partial",
            "steps": [{"name": "semantic_assets", "status": "error", "message": "refresh failed"}],
        },
    )

    exit_code = configure.main(["semantic", "--enable", "--vault", str(vault)])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Configure action: semantic_enable" in output
    assert "Status: partial" in output
    assert "semantic_assets: refresh failed" in output


def test_configure_main_wraps_bootstrap_invariant_failure(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)

    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))
    monkeypatch.setattr(
        configure,
        "handoff_current_script_to_managed_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Created central managed runtime is not Python 3.12+")),
    )

    exit_code = configure.main(["semantic", "--enable", "--vault", str(vault), "--json"])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["steps"][0]["name"] == "managed_runtime"
    assert "Python 3.12+" in payload["steps"][0]["message"]


def test_configure_semantic_enable_propagates_programmer_errors_from_asset_refresh(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)

    monkeypatch.setattr(
        semantic_provision,
        "probe_python",
        lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
    )
    monkeypatch.setattr(
        semantic_provision.semantic_model,
        "provision_semantic_model",
        lambda _vault: _model_outcome(vault, downloaded=False, manifest_changed=False),
    )
    monkeypatch.setattr(
        semantic_provision,
        "refresh_semantic_assets",
        lambda _vault: (_ for _ in ()).throw(TypeError("programmer bug")),
    )

    with pytest.raises(TypeError, match="programmer bug"):
        configure._configure_semantic_enable(vault, provision=True, bootstrap_steps=[])


def test_provision_semantic_runtime_preserves_model_notes(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)

    monkeypatch.setattr(
        semantic_provision,
        "probe_python",
        lambda _python_path, *, modules=(): {"compatible": True, "ok": True, "missing": []},
    )
    monkeypatch.setattr(
        semantic_provision.semantic_model,
        "provision_semantic_model",
        lambda _vault: semantic_model.SemanticModelProvisionOutcome(
            model_name=semantic_model.SHIPPED_MODEL_NAME,
            revision=semantic_model.SHIPPED_MODEL_REVISION,
            local_path="fake-model-path",
            downloaded=True,
            manifest_changed=True,
            notes=("Replaced an unreadable semantic model manifest: bad json",),
        ),
    )
    monkeypatch.setattr(
        semantic_provision,
        "refresh_semantic_assets",
        lambda _vault: [],
    )

    outcome = semantic_provision.provision_semantic_runtime(
        vault,
        python_executable="/managed/python",
        runtime_ok=True,
        refresh_assets=True,
    )

    assert outcome.notes == ["Replaced an unreadable semantic model manifest: bad json"]


def test_sync_runtime_packages_installs_pinned_runtime(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return None

    monkeypatch.setattr(semantic_provision.subprocess, "run", fake_run)

    semantic_provision.sync_runtime_packages("/managed/python")

    assert captured["args"] == [
        "/managed/python",
        "-m",
        "pip",
        "install",
        *semantic_provision.SEMANTIC_RUNTIME_PACKAGES,
    ]
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["timeout"] == semantic_provision.SEMANTIC_RUNTIME_TIMEOUT


def test_load_config_checked_wraps_expected_failures(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)

    def boom(_vault_root):
        raise FileNotFoundError("missing template")

    monkeypatch.setattr(config_module, "load_config", boom)

    with pytest.raises(semantic_config.SemanticConfigLoadError, match="missing template"):
        semantic_config.load_config_checked(vault)


def test_load_config_checked_wraps_import_failures(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)

    monkeypatch.setattr(
        semantic_config.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("missing config module")),
    )

    with pytest.raises(
        semantic_config.SemanticConfigLoadError,
        match="failed to import config module: missing config module",
    ):
        semantic_config.load_config_checked(vault)


def test_load_config_checked_propagates_programmer_errors(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)

    def boom(_vault_root):
        raise TypeError("bad call")

    monkeypatch.setattr(config_module, "load_config", boom)

    with pytest.raises(TypeError, match="bad call"):
        semantic_config.load_config_checked(vault)


def test_configure_workspace_binding_writes_binding_manifest(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(configure, "resolve_local_brain_vault", lambda brain_id: vault if brain_id == "brain" else None)

    exit_code = configure.main([
        "workspace",
        "binding",
        "--vault",
        str(vault),
        "--path",
        str(workspace),
        "--brain",
        "brain",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert (workspace / ".brain" / "local" / "workspace.yaml").read_text(encoding="utf-8") == (
        "brain: brain\nslug: demo-workspace\n"
    )


def test_configure_workspace_metadata_updates_defaults_and_links(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    (workspace / ".brain" / "local").mkdir(parents=True)
    (workspace / ".brain" / "local" / "workspace.yaml").write_text(
        "brain: brain\nslug: demo-workspace\n",
        encoding="utf-8",
    )

    exit_code = configure.main([
        "workspace",
        "metadata",
        "--vault",
        str(vault),
        "--path",
        str(workspace),
        "--tag",
        "workspace/demo",
        "--link",
        "workspace=brain-demo",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert (workspace / ".brain" / "local" / "workspace.yaml").read_text(encoding="utf-8") == (
        "brain: brain\n"
        "slug: demo-workspace\n"
        "defaults:\n"
        "  tags:\n"
        "    - workspace/demo\n"
        "links:\n"
        "  workspace: brain-demo\n"
    )


def test_configure_workspace_bootstrap_installs_agents_and_claude(tmp_path, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    exit_code = configure.main([
        "workspace",
        "bootstrap",
        "--vault",
        str(vault),
        "--path",
        str(workspace),
        "--surface",
        "all",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert "Call MCP `brain_session`" in (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Call brain_session" in (workspace / "CLAUDE.md").read_text(encoding="utf-8")



def test_configure_mcp_returns_structured_result(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    calls = {}

    def fake_apply(vault_root, *, client_arg, scope, target_dir, remove):
        calls["vault_root"] = vault_root
        calls["client_arg"] = client_arg
        calls["scope"] = scope
        calls["target_dir"] = target_dir
        calls["remove"] = remove
        return {
            "status": "changed",
            "warnings": [],
        }

    monkeypatch.setattr(configure.mcp_transport, "apply_mcp_transport_action", fake_apply)

    exit_code = configure.main([
        "mcp",
        "--vault",
        str(vault),
        "--workspace",
        str(workspace),
        "--client",
        "all",
        "--json",
    ])

    assert exit_code == 0
    assert calls == {
        "vault_root": vault,
        "client_arg": "all",
        "scope": "project",
        "target_dir": workspace,
        "remove": False,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["steps"][0]["name"] == "mcp_transport"
    assert payload["steps"][0]["status"] == "changed"
    assert any("/mcp" in note for note in payload["notes"])
    assert any("brain_session" in note for note in payload["notes"])


def test_configure_mcp_remove_noop_returns_noop_step(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(
        configure.mcp_transport,
        "apply_mcp_transport_action",
        lambda *_args, **_kwargs: {"status": "noop", "warnings": []},
    )

    exit_code = configure.main([
        "mcp",
        "--vault",
        str(vault),
        "--workspace",
        str(workspace),
        "--remove",
        "--force",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["status"] == "noop"
    assert "No recorded Brain-managed MCP entries matched this request." == payload["steps"][0]["message"]
    assert payload.get("notes", []) == []


def test_configure_mcp_remove_changed_returns_changed_step(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    monkeypatch.setattr(
        configure.mcp_transport,
        "apply_mcp_transport_action",
        lambda *_args, **_kwargs: {"status": "changed", "warnings": []},
    )

    exit_code = configure.main([
        "mcp",
        "--vault",
        str(vault),
        "--workspace",
        str(workspace),
        "--remove",
        "--force",
        "--json",
    ])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["status"] == "changed"
    assert payload["steps"][0]["message"] == "Removed recorded Brain-managed MCP entries for all (project)."
    assert payload.get("notes", []) == []


def test_configure_mcp_returns_error_when_transport_apply_raises_typed_error(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    workspace = tmp_path / "demo-workspace"
    workspace.mkdir()

    def fake_apply(*_args, **_kwargs):
        raise configure.mcp_transport.InitTransportError("transport failed")

    monkeypatch.setattr(configure.mcp_transport, "apply_mcp_transport_action", fake_apply)

    exit_code = configure.main([
        "mcp",
        "--vault",
        str(vault),
        "--workspace",
        str(workspace),
        "--json",
    ])

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["steps"][0]["status"] == "error"
    assert payload["steps"][0]["message"] == "transport failed"
