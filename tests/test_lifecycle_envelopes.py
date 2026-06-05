"""Tests for lifecycle result envelope metadata."""

from __future__ import annotations

import sys

import configure
import setup as brain_setup
from _lifecycle import semantic_repairs
import _repair_runtime


def test_lifecycle_envelopes_preserve_executable_launch_path(monkeypatch, tmp_path):
    """Managed-python diagnostics should report the launched venv path.

    Linux virtualenvs commonly expose ``bin/python`` as a symlink to the native
    interpreter. These result envelopes are diagnostic-only, so preserving the
    launch path is more useful than collapsing to the interpreter realpath.
    """
    launched_python = tmp_path / "managed" / "bin" / "python"
    launched_python.parent.mkdir(parents=True)
    launched_python.symlink_to(sys.executable)
    vault = tmp_path / "vault"
    vault.mkdir()
    step = {"name": "probe", "status": "noop", "message": "ok"}

    modules_and_calls = (
        (configure, lambda: configure._result_envelope("workspace_binding", vault, [step])),
        (brain_setup, lambda: brain_setup._result_envelope("workspace_setup", vault, [step])),
        (_repair_runtime, lambda: _repair_runtime._finalise_result("runtime", vault, False, [step])),
        (semantic_repairs, lambda: semantic_repairs._finalise_result("semantic", vault, False, [step])),
    )

    for module, _call in modules_and_calls:
        monkeypatch.setattr(module.sys, "executable", str(launched_python))

    for _module, call in modules_and_calls:
        assert call()["managed_python"] == str(launched_python)
