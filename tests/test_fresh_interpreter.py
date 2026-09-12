"""Tests for running lifecycle owners in a fresh interpreter."""

from __future__ import annotations

import os
import subprocess

import pytest

from _lifecycle import fresh_interpreter
from _lifecycle.semantic_rebuild import rebuild_semantic


def test_run_lifecycle_returns_the_owner_result_from_a_child_process(tmp_path):
    result = fresh_interpreter.run_lifecycle_in_fresh_interpreter(
        rebuild_semantic,
        tmp_path,
        dry_run=True,
    )

    assert result["status"] == "planned"
    assert result["dry_run"] is True
    assert result["action"] == "semantic_rebuild"
    assert [step["name"] for step in result["steps"]] == ["semantic_assets"]


def test_run_lifecycle_rejects_owners_outside_the_lifecycle_package(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fresh_interpreter.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("no interpreter should start for an invalid owner"),
    )

    with pytest.raises(ValueError, match="module-level function in _lifecycle"):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(os.system, tmp_path)
    with pytest.raises(ValueError):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(lambda root: {}, tmp_path)


def test_run_lifecycle_surfaces_owner_exceptions_as_typed_errors(tmp_path):
    with pytest.raises(
        fresh_interpreter.FreshInterpreterError,
        match="rebuild_semantic failed with TypeError",
    ):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(
            rebuild_semantic,
            tmp_path,
            dry_run=True,
            unexpected_argument=1,
        )


def test_rebuild_semantic_reports_a_missing_model_as_an_error_step(command_vault_clone):
    pytest.importorskip("numpy")

    result = rebuild_semantic(command_vault_clone.vault_root, dry_run=False)

    assert result["status"] == "error"
    assert result["steps"][0]["name"] == "semantic_assets"
    assert result["steps"][0]["status"] == "error"
    assert "semantic model" in result["steps"][0]["message"]


def test_run_lifecycle_reports_missing_reply(tmp_path, monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "Traceback: boom"

    monkeypatch.setattr(fresh_interpreter.subprocess, "run", lambda *_a, **_k: Completed())

    with pytest.raises(
        fresh_interpreter.FreshInterpreterError,
        match=r"returned no structural result \(exit 1\)",
    ):
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(rebuild_semantic, tmp_path, dry_run=True)


def test_reply_channel_keeps_owner_and_subprocess_output_off_the_reply_stream(capfd):
    with fresh_interpreter.reply_channel() as reply_stream:
        print("owner progress")
        subprocess.run(["sh", "-c", "echo native progress"], check=True)
        reply_stream.write('{"result": 1}')
    print("after")

    captured = capfd.readouterr()
    assert captured.out == '{"result": 1}after\n'
    assert "owner progress" in captured.err
    assert "native progress" in captured.err


def test_selected_interpreter_preserves_venv_symlink(tmp_path, monkeypatch):
    from pathlib import Path
    import sys

    python = tmp_path / "managed/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(Path(sys.executable).resolve())
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, '{"result": {"loaded": true}}', "private diagnostic"
        )

    monkeypatch.setattr(fresh_interpreter.subprocess, "run", run)
    result = fresh_interpreter.run_lifecycle_in_fresh_interpreter(
        rebuild_semantic,
        tmp_path,
        python_executable=python,
        timeout=12,
    )
    assert calls[0][0] == str(python)
    assert calls[0][0] != str(python.resolve())
    assert result == {"loaded": True}


@pytest.mark.parametrize(
    "stdout, exit_code",
    [
        ("null", 0),
        ("[]", 0),
        ("{}", 0),
        ('{"result": {}}', 1),
        ('{"error": []}', 0),
        ('{"result": {}, "error": {}}', 0),
    ],
)
def test_malformed_reply_is_bounded_and_never_exposes_stderr(
    tmp_path, monkeypatch, stdout, exit_code
):
    monkeypatch.setattr(
        fresh_interpreter.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            [], exit_code, stdout, "PRIVATE-DIAGNOSTIC"
        ),
    )
    with pytest.raises(fresh_interpreter.FreshInterpreterError) as exc:
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(rebuild_semantic, tmp_path)
    assert "PRIVATE-DIAGNOSTIC" not in str(exc.value)


def test_child_timeout_is_bounded_and_content_free(tmp_path, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(
            "private command", 7, output="PRIVATE-OUTPUT", stderr="PRIVATE-ERROR"
        )

    monkeypatch.setattr(fresh_interpreter.subprocess, "run", timeout)
    with pytest.raises(
        fresh_interpreter.FreshInterpreterError, match="within 7 seconds"
    ) as exc:
        fresh_interpreter.run_lifecycle_in_fresh_interpreter(
            rebuild_semantic, tmp_path, timeout=7
        )
    assert "PRIVATE" not in str(exc.value)
