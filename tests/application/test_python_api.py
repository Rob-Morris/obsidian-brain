"""Supported typed-Python façade and import-boundary contracts."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import subprocess
import sys

import brain_application
import brain_application.context as public_context
import brain_application.documents as public_documents
import brain_application.requests as public_requests
import brain_application.results as public_results


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_kernel_facade_does_not_import_command_owner_modules():
    script = """
import sys
import brain_application
owners = sorted(
    name for name in sys.modules
    if name.startswith('_application.') and name.count('.') > 1
)
print('\\n'.join(owners))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT / "src" / "brain-core" / "scripts"),
        },
        capture_output=True,
        text=True,
        check=True,
    )

    assert completed.stdout.strip() == ""


def test_public_facades_export_only_declared_supported_symbols():
    assert set(brain_application.__all__) == {
        "CommandApplication",
        "CommandResult",
        "Error",
        "InvocationContext",
        "Ok",
        "Partial",
        "SelectedBrain",
    }
    assert "execute" not in public_documents.__all__
    assert "decode" not in public_documents.__all__
    assert "catalogue_entry" not in public_documents.__all__
    assert all(
        name == "CommandRequest" or name.endswith("Request")
        for name in public_requests.__all__
    )


def test_supported_kernel_and_context_types_have_behavioural_docstrings():
    symbols = (
        brain_application.CommandApplication,
        brain_application.CommandApplication.invoke,
        brain_application.InvocationContext,
        brain_application.SelectedBrain,
        brain_application.Ok,
        brain_application.Partial,
        brain_application.Error,
        *(getattr(public_context, name) for name in public_context.__all__),
        *(getattr(public_results, name) for name in public_results.__all__ if name != "CommandResult"),
    )

    undocumented = sorted(
        {symbol.__qualname__ for symbol in symbols if not inspect.getdoc(symbol)}
    )
    assert undocumented == []
