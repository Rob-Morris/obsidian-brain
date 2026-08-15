"""Supported typed-Python façade and import-boundary contracts."""

from __future__ import annotations

import dataclasses
import inspect
import os
from pathlib import Path
import subprocess
import sys
import types
import typing

import brain_application
import brain_application.context as public_context
import brain_application.documents as public_documents
import brain_application.requests as public_requests
import brain_application.results as public_results
import brain_application.values as public_values


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


def test_every_request_construction_value_has_a_supported_public_export():
    public_by_type = {
        getattr(public_values, name): name for name in public_values.__all__
    }
    discovered = set()
    pending = []

    def discover(annotation):
        origin = typing.get_origin(annotation)
        if origin in {
            typing.Union,
            types.UnionType,
            tuple,
            list,
            dict,
            set,
            frozenset,
            typing.Literal,
        }:
            for argument in typing.get_args(annotation):
                discover(argument)
        elif (
            isinstance(annotation, type)
            and annotation.__module__.startswith("_application")
            and annotation not in discovered
        ):
            discovered.add(annotation)
            pending.append(annotation)

    for name in public_requests.__all__:
        if name == "CommandRequest":
            continue
        request_type = getattr(public_requests, name)
        hints = typing.get_type_hints(request_type)
        for field in dataclasses.fields(request_type):
            discover(hints[field.name])
    while pending:
        value_type = pending.pop()
        if not dataclasses.is_dataclass(value_type):
            continue
        hints = typing.get_type_hints(value_type)
        for field in dataclasses.fields(value_type):
            discover(hints[field.name])

    private_only = sorted(
        f"{value.__module__}.{value.__name__}"
        for value in discovered
        if value not in public_by_type
    )
    assert private_only == []


def test_fresh_process_can_construct_context_and_invoke_read_and_mutation(
    command_vault_clone,
):
    script = r'''
from datetime import datetime, timezone
from pathlib import Path
import sys

from brain_application import CommandApplication, InvocationContext, SelectedBrain
from brain_application.context import (
    Authority,
    CapabilitySnapshot,
    DependencyTier,
    MemoryReceiptStore,
    ProviderBindings,
    SnapshotFreshness,
)
from brain_application.documents import (
    DocumentLocator,
    DocumentResource,
    DocumentWriteOperation,
    DocumentWriteRequest,
    InlineContent,
)
from brain_application.requests import ArtefactReadRequest

PATH = "Designs/project~command-fixture/Command Fixture Design.md"

class Clock:
    def now(self):
        return datetime.now(timezone.utc)

class AuthorityEvaluator:
    def allows(self, *, command_id, required, effect):
        return True
    def ceiling_allows(self, command_id):
        return True
    def consume(self, command_id):
        return True

root = Path(sys.argv[1])
clock = Clock()
receipts = MemoryReceiptStore(clock)

def application(invocation_id):
    context = InvocationContext(
        SelectedBrain("test-brain", root),
        "administrator",
        AuthorityEvaluator(),
        DependencyTier.MANAGED,
        CapabilitySnapshot(
            "fresh-process",
            SnapshotFreshness.FRESH,
            clock.now(),
        ),
        ProviderBindings(),
        "fresh-process",
        invocation_id,
        receipts,
        receipts,
        clock,
    )
    return CommandApplication(context)

read = application("read-invocation").invoke(ArtefactReadRequest(PATH))
assert read.status == "ok", read
mutation = application("write-invocation").invoke(
    DocumentWriteRequest(
        DocumentLocator(DocumentResource.ARTEFACT, PATH),
        read.result.revision,
        DocumentWriteOperation.APPEND,
        InlineContent("\nTyped façade mutation.\n"),
    )
)
assert mutation.status == "ok", mutation
assert "Typed façade mutation." in (root / PATH).read_text(encoding="utf-8")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(command_vault_clone.vault_root)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT / "src" / "brain-core" / "scripts"),
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
