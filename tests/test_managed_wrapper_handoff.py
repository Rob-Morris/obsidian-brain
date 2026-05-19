"""Managed-wrapper handoff contract tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

import _bootstrap.runtime as bootstrap_runtime


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
