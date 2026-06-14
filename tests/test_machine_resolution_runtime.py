"""Machine-level Brain resolution runtime deployment and payload tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from _machine.resolve_brain import RESOLUTION_RUNTIME_VERSION as ENTRY_VERSION
from _machine.resolve_brain import resolve_payload
from _machine.resolution_runtime import (
    RESOLUTION_RUNTIME_VERSION as DEPLOY_VERSION,
    deployed_version,
    ensure_resolution_runtime,
    resolution_runtime_entry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "src" / "brain-core" / "scripts"


def test_resolution_runtime_version_constants_stay_in_sync():
    assert ENTRY_VERSION == DEPLOY_VERSION


def test_resolution_runtime_deploys_stdlib_resolver_and_version_stamp(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"

    result = ensure_resolution_runtime(SCRIPTS_DIR, runtime_root=runtime_root)

    assert result["status"] == "changed"
    assert result["entry"] == str(resolution_runtime_entry(runtime_root))
    assert deployed_version(runtime_root) == result["version"]
    assert (runtime_root / "resolve_brain.py").is_file()
    assert (runtime_root / "_bootstrap" / "workspace_binding.py").is_file()
    assert (runtime_root / "_common" / "_vault.py").is_file()
    assert not (runtime_root / "_common" / "__init__.py").exists()

    second = ensure_resolution_runtime(SCRIPTS_DIR, runtime_root=runtime_root)
    assert second["status"] == "noop"

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    proc = subprocess.run(
        [sys.executable, str(runtime_root / "resolve_brain.py"), "--start-dir", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["status"] == "degraded"
    assert payload["vault_root"] is None
    assert payload["session_resolution"]["code"] == "no_brain"


def test_resolution_runtime_refuses_partial_deploy_when_source_closure_missing(tmp_path):
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS_DIR, scripts)
    missing = scripts / "_common" / "_vault.py"
    missing.unlink()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    existing_entry = runtime_root / "resolve_brain.py"
    existing_entry.write_text("old runtime\n")

    result = ensure_resolution_runtime(scripts, runtime_root=runtime_root)

    assert result["status"] == "error"
    assert "_common/_vault.py" in result["message"]
    assert result["changed_files"] == []
    assert existing_entry.read_text() == "old runtime\n"
    assert deployed_version(runtime_root) is None


def test_deployed_resolution_runtime_import_closure_is_self_contained(tmp_path):
    runtime_root = tmp_path / "runtime"
    ensure_resolution_runtime(SCRIPTS_DIR, runtime_root=runtime_root)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resolve_brain; import _bootstrap.workspace_binding; import vault_registry",
        ],
        cwd=runtime_root,
        env={"PYTHONPATH": str(runtime_root)},
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (runtime_root / "_common" / "__init__.py").exists()


def test_deployed_resolution_runtime_resolves_registered_workspace_brain(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    ensure_resolution_runtime(SCRIPTS_DIR, runtime_root=runtime_root)
    home = tmp_path / "home"
    home.mkdir()
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    vault = tmp_path / "brain"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("1.0.0\n")
    registry.write_text(f"brain\tlocal\t{vault}\n")
    workspace = tmp_path / "workspace"
    (workspace / ".brain" / "local").mkdir(parents=True)
    (workspace / ".brain" / "local" / "workspace.yaml").write_text("brain: brain\nslug: ws\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(workspace))
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)

    proc = subprocess.run(
        [sys.executable, str(runtime_root / "resolve_brain.py"), "--start-dir", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["status"] == "ok"
    assert payload["target"] == {
        "kind": "local",
        "vault_root": str(vault),
        "workspace_dir": str(workspace),
        "source": "workspace_env",
    }


def test_resolve_payload_uses_real_error_code_and_degraded_session_shape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)

    payload = resolve_payload(start_dir=tmp_path)

    assert payload["status"] == "degraded"
    assert payload["recovery_class"] == "session_resolution"
    assert payload["vault_root"] is None
    assert payload["session_resolution"]["code"] == "no_brain"
    assert payload["session_resolution"]["context"] == {
        "workspace_env": None,
        "vault_root_env": None,
        "start_dir": str(tmp_path),
        "workspace_anchor_explicit": False,
        "vault_root_explicit": False,
    }


def test_resolve_payload_surfaces_invalid_binding_from_malformed_workspace_manifest(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    manifest_dir = workspace / ".brain" / "local"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "workspace.yaml").write_text("\tbrain: broken\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(workspace))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)

    payload = resolve_payload(start_dir=tmp_path)

    assert payload["status"] == "degraded"
    assert payload["recovery_class"] == "session_resolution"
    assert payload["vault_root"] is None
    assert payload["session_resolution"]["code"] == "invalid_binding"
    assert "failed to load .brain/local/workspace.yaml" in payload["message"]
    assert payload["session_resolution"]["context"]["workspace_env"] == str(workspace)


def test_resolve_payload_treats_slug_only_manifest_as_missing_binding(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    manifest_dir = workspace / ".brain" / "local"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "workspace.yaml").write_text("slug: workspace-only\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(workspace))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)

    payload = resolve_payload(start_dir=tmp_path)

    assert payload["status"] == "degraded"
    assert payload["recovery_class"] == "session_resolution"
    assert payload["vault_root"] is None
    assert payload["session_resolution"]["code"] == "no_brain"
    context = payload["session_resolution"]["context"]
    assert context["workspace_env"] == str(workspace)
    assert context["workspace_anchor_explicit"] is True
    assert "has no Brain binding" in payload["message"]


def test_no_anchor_stale_default_gets_combined_workspace_and_machine_guidance(tmp_path, monkeypatch):
    home = tmp_path / "home"
    default_dir = home / ".config" / "brain"
    default_dir.mkdir(parents=True)
    (default_dir / "default").write_text("missing-brain\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)

    payload = resolve_payload(start_dir=tmp_path)

    assert payload["status"] == "degraded"
    assert payload["session_resolution"]["code"] == "stale_binding"
    action = payload["recovery"]["action"]
    assert "workspace" in action
    assert "machine default Brain" in action
