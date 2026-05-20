"""Managed-wrapper handoff contract tests."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import _bootstrap.runtime as bootstrap_runtime
from _common import _venv as venv_helper


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "src" / "brain-core" / "scripts"


def _load_script_module(script_name: str):
    path = SCRIPTS_DIR / script_name
    module_name = f"_test_{path.stem}_wrapper"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _ReexecCalled(Exception):
    """Sentinel for non-returning handoff calls in unit tests."""


def test_shared_handoff_short_circuits_when_already_in_managed_runtime(monkeypatch):
    monkeypatch.setenv("BRAIN_MANAGED_RUNTIME", "1")
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not bootstrap")),
    )

    summary = bootstrap_runtime.handoff_current_script_to_managed_runtime(
        "/tmp/vault",
        dependency_owner="search_index.py",
        forwarded_args=["query"],
        script_path="/tmp/search_index.py",
    )

    assert summary["managed_runtime_ready"] is True
    assert summary["managed_python"] == sys.executable


def test_shared_handoff_reexecs_when_bootstrap_returns_different_python(monkeypatch):
    monkeypatch.delenv("BRAIN_MANAGED_RUNTIME", raising=False)
    monkeypatch.setattr(
        bootstrap_runtime,
        "current_process_in_managed_runtime",
        lambda _vault: False,
    )
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: {
            "managed_runtime_ready": True,
            "managed_python": "/managed/python",
            "steps": [],
        },
    )

    captured = {}

    def fake_exec(*, managed_python, script_path, forwarded_args, summary):
        captured["managed_python"] = managed_python
        captured["script_path"] = script_path
        captured["forwarded_args"] = forwarded_args
        captured["summary"] = summary
        raise _ReexecCalled

    monkeypatch.setattr(bootstrap_runtime, "exec_managed_runtime", fake_exec)

    with pytest.raises(_ReexecCalled):
        bootstrap_runtime.handoff_current_script_to_managed_runtime(
            "/tmp/vault",
            dependency_owner="build_index.py",
            forwarded_args=["--json"],
            script_path="/tmp/build_index.py",
        )

    assert captured["managed_python"] == "/managed/python"
    assert captured["script_path"] == "/tmp/build_index.py"
    assert captured["forwarded_args"] == ["--json"]


def test_shared_handoff_raises_when_bootstrap_cannot_produce_runtime(monkeypatch):
    monkeypatch.delenv("BRAIN_MANAGED_RUNTIME", raising=False)
    monkeypatch.setattr(
        bootstrap_runtime,
        "current_process_in_managed_runtime",
        lambda _vault: False,
    )
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: {
            "managed_runtime_ready": False,
            "managed_python": "",
            "steps": [],
        },
    )

    with pytest.raises(RuntimeError, match="did not produce a usable central venv"):
        bootstrap_runtime.handoff_current_script_to_managed_runtime(
            "/tmp/vault",
            dependency_owner="evaluate_search.py",
            forwarded_args=["--benchmark", "bench.json"],
            script_path="/tmp/evaluate_search.py",
        )


def test_ensure_managed_runtime_preserves_bootstrap_error_message(monkeypatch):
    monkeypatch.delenv("BRAIN_MANAGED_RUNTIME", raising=False)
    monkeypatch.delenv("BRAIN_SKIP_BOOTSTRAP", raising=False)
    monkeypatch.setattr(
        bootstrap_runtime,
        "current_process_in_managed_runtime",
        lambda _vault: False,
    )
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: {
            "managed_runtime_ready": False,
            "managed_python": "",
            "message": "ensure_central_venv failed: command failed: pip install (exit 1)\npip install failed",
            "steps": [],
        },
    )

    with pytest.raises(RuntimeError, match="pip install failed"):
        bootstrap_runtime.ensure_managed_runtime(
            "/tmp/vault",
            dependency_owner="init.py",
            required_modules=("mcp",),
        )


def test_preview_managed_runtime_short_circuits_when_already_in_managed_runtime(monkeypatch):
    monkeypatch.setenv("BRAIN_MANAGED_RUNTIME", "1")
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not bootstrap")),
    )

    summary = bootstrap_runtime.preview_managed_runtime(
        "/tmp/vault",
        dependency_owner="repair.py",
        required_modules=(),
    )

    assert summary["managed_runtime_ready"] is True
    assert summary["managed_python"] == sys.executable


def test_semantic_scope_requires_only_baseline_managed_runtime_modules():
    assert bootstrap_runtime.required_modules_for_scope("semantic") == bootstrap_runtime.MANAGED_RUNTIME_REQUIRED_MODULES


def test_preview_managed_runtime_short_circuits_when_skip_bootstrap_current_interpreter_is_sufficient(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_BOOTSTRAP", "1")
    monkeypatch.delenv("BRAIN_MANAGED_RUNTIME", raising=False)
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not bootstrap")),
    )

    summary = bootstrap_runtime.preview_managed_runtime(
        "/tmp/vault",
        dependency_owner="repair.py",
        required_modules=(),
    )

    assert summary["managed_runtime_ready"] is True
    assert summary["managed_python"] == sys.executable


def test_preview_managed_runtime_does_not_trust_skip_bootstrap_when_current_interpreter_lacks_scope_requirements(monkeypatch):
    monkeypatch.setenv("BRAIN_SKIP_BOOTSTRAP", "1")
    monkeypatch.delenv("BRAIN_MANAGED_RUNTIME", raising=False)
    monkeypatch.setattr(
        bootstrap_runtime,
        "current_process_in_managed_runtime",
        lambda _vault: False,
    )
    captured = {}

    def fake_bootstrap(vault_root, *, required_modules, dependency_owner, launcher_python=None, timeout=300, dry_run=False):
        captured["vault_root"] = vault_root
        captured["required_modules"] = required_modules
        captured["dependency_owner"] = dependency_owner
        captured["dry_run"] = dry_run
        return {
            "managed_runtime_ready": False,
            "managed_python": "",
            "status": "planned",
            "steps": [],
        }

    monkeypatch.setattr(bootstrap_runtime, "bootstrap_managed_runtime", fake_bootstrap)

    summary = bootstrap_runtime.preview_managed_runtime(
        "/tmp/vault",
        dependency_owner="repair.py",
        required_modules=("definitely_missing_module",),
    )

    assert captured["dependency_owner"] == "repair.py"
    assert captured["required_modules"] == ("definitely_missing_module",)
    assert captured["dry_run"] is True
    assert summary["status"] == "planned"


def test_preview_managed_runtime_delegates_to_bootstrap_dry_run(monkeypatch):
    monkeypatch.delenv("BRAIN_MANAGED_RUNTIME", raising=False)
    monkeypatch.delenv("BRAIN_SKIP_BOOTSTRAP", raising=False)
    monkeypatch.setattr(
        bootstrap_runtime,
        "current_process_in_managed_runtime",
        lambda _vault: False,
    )
    captured = {}

    def fake_bootstrap(vault_root, *, required_modules, dependency_owner, launcher_python=None, timeout=300, dry_run=False):
        captured["vault_root"] = str(vault_root)
        captured["required_modules"] = required_modules
        captured["dependency_owner"] = dependency_owner
        captured["launcher_python"] = launcher_python
        captured["timeout"] = timeout
        captured["dry_run"] = dry_run
        return {
            "managed_runtime_ready": False,
            "managed_python": "",
            "status": "planned",
            "steps": [{"name": "managed_runtime", "status": "planned", "message": "Would create runtime."}],
        }

    monkeypatch.setattr(bootstrap_runtime, "bootstrap_managed_runtime", fake_bootstrap)

    summary = bootstrap_runtime.preview_managed_runtime(
        "/tmp/vault",
        dependency_owner="repair.py",
        required_modules=("mcp",),
        launcher_python="/launcher/python",
        timeout=42,
    )

    assert captured == {
        "vault_root": "/tmp/vault",
        "required_modules": ("mcp",),
        "dependency_owner": "repair.py",
        "launcher_python": "/launcher/python",
        "timeout": 42,
        "dry_run": True,
    }
    assert summary["status"] == "planned"


def test_shared_handoff_reexecs_end_to_end_through_fake_managed_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "_home"))

    vault = tmp_path / "vault"
    (vault / ".brain-core" / "brain_mcp").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.42.3\n")
    (vault / ".brain-core" / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")

    managed_python = venv_helper.resolve_vault_venv_python(vault)
    managed_python.parent.mkdir(parents=True, exist_ok=True)
    marker_path = tmp_path / "managed-marker.txt"
    output_path = tmp_path / "handoff-output.json"
    managed_python.write_text(
        "#!/bin/sh\n"
        f"echo \"$0\" >> {marker_path}\n"
        f"exec {sys.executable} \"$@\"\n",
        encoding="utf-8",
    )
    managed_python.chmod(
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        | stat.S_IRGRP | stat.S_IXGRP
        | stat.S_IROTH | stat.S_IXOTH
    )

    probe_script = tmp_path / "handoff_probe.py"
    probe_script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "from _bootstrap.runtime import handoff_current_script_to_managed_runtime, load_bootstrap_steps\n"
        "vault_root = sys.argv[1]\n"
        "handoff_current_script_to_managed_runtime(\n"
        "    vault_root,\n"
        "    dependency_owner='handoff_probe.py',\n"
        "    forwarded_args=sys.argv[1:],\n"
        "    script_path=os.path.abspath(__file__),\n"
        "    required_modules=(),\n"
        ")\n"
        "payload = {\n"
        "    'managed_env': os.environ.get('BRAIN_MANAGED_RUNTIME'),\n"
        "    'managed_python': sys.executable,\n"
        "    'steps': load_bootstrap_steps(),\n"
        "}\n"
        "Path(os.environ['TEST_OUTPUT']).write_text(json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS_DIR)
    env["TEST_OUTPUT"] = str(output_path)
    env.pop("BRAIN_MANAGED_RUNTIME", None)
    env.pop("BRAIN_BOOTSTRAP_SUMMARY", None)

    result = subprocess.run(
        [sys.executable, str(probe_script), str(vault)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    marker_lines = [
        line
        for line in marker_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert marker_lines
    assert marker_lines[-1] == str(managed_python)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["managed_env"] == "1"
    assert payload["steps"]


@pytest.mark.parametrize(
    ("script_name", "argv"),
    [
        ("search_index.py", ["search_index.py", "query"]),
        ("build_index.py", ["build_index.py"]),
        ("evaluate_search.py", ["evaluate_search.py", "--benchmark", "bench.json"]),
        (
            "construct_benchmark_fixture.py",
            ["construct_benchmark_fixture.py", "--fixture-out", "fixture.json"],
        ),
        ("compile_router.py", ["compile_router.py"]),
        ("compile_colours.py", ["compile_colours.py"]),
        ("sync_definitions.py", ["sync_definitions.py"]),
        (
            "shape_printable.py",
            ["shape_printable.py", "--source", "Notes/source.md", "--slug", "demo-printable"],
        ),
        (
            "shape_presentation.py",
            ["shape_presentation.py", "--source", "Notes/source.md", "--slug", "demo-deck"],
        ),
        ("migrate_naming.py", ["migrate_naming.py"]),
    ],
)
def test_managed_wrappers_attempt_handoff_before_substantive_work(
    script_name,
    argv,
    monkeypatch,
):
    module = _load_script_module(script_name)
    monkeypatch.setattr(module.sys, "argv", argv)
    monkeypatch.setattr(
        module,
        "find_vault_root",
        lambda *args, **kwargs: "/tmp/vault",
    )

    captured = {}

    def fake_handoff(vault_root, *, dependency_owner, forwarded_args, script_path, required_modules):
        captured["vault_root"] = vault_root
        captured["dependency_owner"] = dependency_owner
        captured["forwarded_args"] = forwarded_args
        captured["script_path"] = script_path
        captured["required_modules"] = required_modules
        raise _ReexecCalled

    monkeypatch.setattr(module, "handoff_current_script_to_managed_runtime", fake_handoff)

    with pytest.raises(_ReexecCalled):
        module.main()

    assert captured["vault_root"] == "/tmp/vault"
    assert captured["dependency_owner"] == script_name
    assert captured["forwarded_args"] == argv[1:]
    assert captured["script_path"].endswith(script_name)
    assert captured["required_modules"] == ("mcp",)


@pytest.mark.parametrize(
    ("script_name", "argv"),
    [
        ("search_index.py", ["search_index.py", "query"]),
        ("build_index.py", ["build_index.py"]),
        ("evaluate_search.py", ["evaluate_search.py", "--benchmark", "bench.json"]),
        (
            "construct_benchmark_fixture.py",
            ["construct_benchmark_fixture.py", "--fixture-out", "fixture.json"],
        ),
        ("compile_router.py", ["compile_router.py"]),
        ("compile_colours.py", ["compile_colours.py"]),
        ("sync_definitions.py", ["sync_definitions.py"]),
        (
            "shape_printable.py",
            ["shape_printable.py", "--source", "Notes/source.md", "--slug", "demo-printable"],
        ),
        (
            "shape_presentation.py",
            ["shape_presentation.py", "--source", "Notes/source.md", "--slug", "demo-deck"],
        ),
        ("migrate_naming.py", ["migrate_naming.py"]),
    ],
)
def test_managed_wrappers_surface_runtime_repair_guidance_on_bootstrap_failure(
    script_name,
    argv,
    monkeypatch,
    capsys,
):
    module = _load_script_module(script_name)
    monkeypatch.setattr(module.sys, "argv", argv)
    monkeypatch.setattr(
        module,
        "find_vault_root",
        lambda *args, **kwargs: "/tmp/vault",
    )
    monkeypatch.setattr(
        module,
        "handoff_current_script_to_managed_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("managed runtime unavailable")),
    )

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert excinfo.value.code == 1
    stderr = capsys.readouterr().err
    assert "managed runtime unavailable" in stderr
    assert "repair.py runtime" in stderr
