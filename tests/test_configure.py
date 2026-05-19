"""Tests for scripts/configure.py semantic setup flow."""

from __future__ import annotations

import json
import subprocess
import pytest

import config as config_module
import configure
import _bootstrap.runtime as bootstrap_runtime
import _lifecycle_common as lifecycle_common
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
    (bc / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\npyyaml>=6.0\n")
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


def test_bootstrap_summary_creates_runtime_and_syncs_dependencies(tmp_path, monkeypatch):
    """`_bootstrap_summary` produces the correct envelope when a brand-new runtime is created.

    Post-v0.39.0, `bootstrap_managed_runtime` delegates all resolve/reuse/sync/create
    logic to `_common._venv.resolve_or_provision_central_venv` — the single
    owner across entry points. We patch that owner directly to assert the
    envelope-building behaviour in isolation.
    """
    vault = _make_vault(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    central_python = fake_home / ".brain" / "venvs" / "py3.12-fake" / "bin" / "python"
    central_python.parent.mkdir(parents=True, exist_ok=True)
    central_python.write_text("#!/usr/bin/env python\n")

    def fake_provision(*_args, **_kwargs):
        from _common import _venv as _venv_module
        return {
            "outcome": _venv_module.RUNTIME_CREATED,
            "python": str(central_python),
            "venv_dir": str(central_python.parent.parent),
            "python_tag": "py3.12",
            "hash": "fake",
            "missing_modules": (),
        }

    monkeypatch.setattr(configure, "find_launcher_python", lambda: "/fake/python3.12")
    monkeypatch.setattr(bootstrap_runtime, "resolve_or_provision_central_venv", fake_provision)

    summary = configure._bootstrap_summary(vault)

    assert summary["status"] == "ready"
    assert [step["name"] for step in summary["steps"]] == ["managed_runtime", "managed_dependencies"]
    assert summary["steps"][0]["status"] == "changed"
    assert summary["steps"][1]["status"] == "noop"


def test_bootstrap_summary_requires_compatible_launcher(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(configure, "find_launcher_python", lambda: None)

    with pytest.raises(RuntimeError, match="Python 3.12\\+"):
        configure._bootstrap_summary(vault)


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
        lifecycle_common.probe_python("/fake/python")


def test_configure_main_uses_bootstrap_steps_from_managed_env(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)
    bootstrap_steps = [{"name": "managed_runtime", "status": "noop", "message": "ready"}]
    captured = {}

    monkeypatch.setenv(configure.CONFIGURE_MANAGED_ENV, "1")
    monkeypatch.setenv(
        configure.CONFIGURE_BOOTSTRAP_SUMMARY_ENV,
        json.dumps({"steps": bootstrap_steps}),
    )
    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))

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

    monkeypatch.setenv(configure.CONFIGURE_MANAGED_ENV, "1")
    monkeypatch.setenv(
        configure.CONFIGURE_BOOTSTRAP_SUMMARY_ENV,
        json.dumps({"steps": [{"name": "managed_runtime", "status": "noop", "message": "ready"}]}),
    )
    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))
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


def test_configure_main_rejects_corrupt_bootstrap_summary(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)

    monkeypatch.setenv(configure.CONFIGURE_MANAGED_ENV, "1")
    monkeypatch.setenv(configure.CONFIGURE_BOOTSTRAP_SUMMARY_ENV, "{not-json")
    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))

    with pytest.raises(RuntimeError, match="bootstrap summary"):
        configure.main(["semantic", "--enable", "--vault", str(vault)])


def test_configure_main_wraps_bootstrap_invariant_failure(tmp_path, monkeypatch, capsys):
    vault = _make_vault(tmp_path)

    monkeypatch.delenv(configure.CONFIGURE_MANAGED_ENV, raising=False)
    monkeypatch.setattr(configure, "find_vault_root", lambda _vault: str(vault))
    monkeypatch.setattr(
        configure,
        "_bootstrap_summary",
        lambda _vault_root: (_ for _ in ()).throw(AssertionError("Created vault-local .venv is not Python 3.12+")),
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
