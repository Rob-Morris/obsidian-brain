"""Behaviour for stdlib-safe machine-global launcher read owners."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
import ast
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import (
    CommandError,
    CommittedEffect,
    Error,
    ErrorCode,
    Ok,
    OutcomeReference,
    Partial,
    ReceiptState,
)
from _launcher.invocation import LauncherInvocation
from _launcher.managed_runtime import (
    RuntimeResolveRequest,
    RuntimeResolveRunnableRequest,
)
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.registry import (
    BrainGetDefaultRequest,
    BrainListRequest,
    BrainResolveRequest,
)
from _launcher.version import BrainVersionRequest
from _application import receipts as application_receipts
from _application import results as application_results
import vault_registry


NOW = datetime.fromisoformat("2026-08-10T03:00:00+10:00")


class _Authority:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def allows(self, **kwargs):
        self.calls.append(kwargs)
        return self.allowed


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _invocation(tmp_path, *, authority=None, receipts=None):
    context = LauncherContext(
        profile="reader",
        authority=authority or _Authority(),
        providers=ProviderBindings(),
        correlation_id="corr-launcher",
        invocation_id="inv-launcher",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        cli_version="1.2.0",
        launcher_python=Path(sys.executable).resolve(),
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def test_launcher_read_owners_match_their_authoritative_catalogue_entries():
    entries = {entry.command_id: entry for entry in LAUNCHER_CATALOGUE.entries}

    assert [owner.command_id for owner in LAUNCHER_OWNERS.entries] == [
        "brain.get-default",
        "brain.list",
        "brain.resolve",
        "brain.version",
        "runtime.resolve",
        "runtime.resolve-runnable",
    ]
    for owner in LAUNCHER_OWNERS.entries:
        entry = entries[owner.command_id]
        assert entry.owner_ref == owner.owner_ref
        assert entry.command_version == owner.command_version
        assert entry.authority == "reader"
        assert entry.effect_class == "none"
        assert entry.retry_class == "safe"
        assert entry.required_providers == ()


def test_launcher_registry_reads_return_typed_machine_state(vault, tmp_path):
    brain_id = vault_registry.register(str(vault), brain_id="test-brain")
    vault_registry.set_default(brain_id)
    invocation = _invocation(tmp_path)

    default = invocation.invoke(BrainGetDefaultRequest())
    listed = invocation.invoke(BrainListRequest())
    resolved = invocation.invoke(BrainResolveRequest("test-brain"))

    assert default.status == "ok"
    assert default.result.brain_id == "test-brain"
    assert listed.status == "ok"
    assert len(listed.result.entries) == 1
    assert listed.result.entries[0].brain_id == "test-brain"
    assert listed.result.entries[0].value == str(vault.resolve())
    assert listed.result.entries[0].is_default is True
    assert resolved.status == "ok"
    assert resolved.result.vault_root == str(vault.resolve())


def test_launcher_registry_resolve_distinguishes_absence_from_read_conflict(
    tmp_path,
    monkeypatch,
):
    invocation = _invocation(tmp_path)

    missing = invocation.invoke(BrainResolveRequest("missing-brain"))
    monkeypatch.setattr(
        vault_registry,
        "resolve",
        lambda _brain_id: (_ for _ in ()).throw(
            vault_registry.RegistryReadError("registry unreadable")
        ),
    )
    conflict = invocation.invoke(BrainResolveRequest("missing-brain"))

    assert missing.error.code is ErrorCode.NOT_FOUND
    assert missing.effects == "none"
    assert conflict.error.code is ErrorCode.CONFLICT
    assert conflict.effects == "none"


def test_launcher_version_returns_static_manifest_identity(tmp_path):
    result = _invocation(tmp_path).invoke(BrainVersionRequest())

    assert result.status == "ok"
    assert result.result.cli_version == "1.2.0"
    assert result.result.launcher_catalogue_schema == "brain.launcher-catalogue/1"
    assert result.result.launcher_catalogue_fingerprint == LAUNCHER_CATALOGUE.fingerprint
    assert result.result.launcher_command_count == 23


def test_runtime_read_owners_delegate_to_bootstrap_runtime_semantics(
    vault,
    tmp_path,
    monkeypatch,
):
    from _common import _venv

    expected = tmp_path / "managed" / "bin" / "python"
    runnable = Path(sys.executable).resolve()
    monkeypatch.setattr(_venv, "resolve_vault_venv_python", lambda *_a, **_k: expected)
    monkeypatch.setattr(_venv, "find_runnable_python", lambda *_a, **_k: runnable)
    invocation = _invocation(tmp_path)

    resolved = invocation.invoke(RuntimeResolveRequest(vault.resolve()))
    available = invocation.invoke(RuntimeResolveRunnableRequest(vault.resolve()))

    assert resolved.status == "ok"
    assert resolved.result.python == str(expected)
    assert resolved.result.exists is False
    assert available.status == "ok"
    assert available.result.python == str(runnable)


def test_runtime_runnable_absence_is_a_known_no_effect(tmp_path, vault, monkeypatch):
    from _common import _venv

    monkeypatch.setattr(_venv, "find_runnable_python", lambda *_a, **_k: None)

    result = _invocation(tmp_path).invoke(
        RuntimeResolveRunnableRequest(vault.resolve())
    )

    assert result.status == "error"
    assert result.error.code is ErrorCode.NOT_FOUND
    assert result.effects == "none"


def test_launcher_authority_denial_precedes_owner_execution(tmp_path, monkeypatch):
    authority = _Authority(allowed=False)
    receipts = _Receipts()
    monkeypatch.setattr(
        vault_registry,
        "get_default",
        lambda: pytest.fail("denied launcher owner must not execute"),
    )

    result = _invocation(
        tmp_path,
        authority=authority,
        receipts=receipts,
    ).invoke(BrainGetDefaultRequest())

    assert result.error.code is ErrorCode.AUTHORITY_DENIED
    assert result.effects == "none"
    assert authority.calls == [
        {
            "command_id": "brain.get-default",
            "required": "reader",
            "effect": "none",
        }
    ]
    assert receipts.values[-1].state is ReceiptState.NONE


def test_unexpected_read_owner_failure_is_privacy_bounded_internal_error(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        vault_registry,
        "get_default",
        lambda: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )

    result = _invocation(tmp_path).invoke(BrainGetDefaultRequest())

    assert result.error.code is ErrorCode.INTERNAL_ERROR
    assert result.error.message == "The launcher command failed unexpectedly."
    assert "secret" not in repr(result)


def test_launcher_result_vocabulary_matches_shared_command_result_structure():
    pairs = (
        (Ok, application_results.Ok),
        (Partial, application_results.Partial),
        (Error, application_results.Error),
        (CommandError, application_results.CommandError),
        (CommittedEffect, application_receipts.CommittedEffect),
        (OutcomeReference, application_receipts.OutcomeReference),
    )

    for launcher_type, application_type in pairs:
        assert [field.name for field in fields(launcher_type)] == [
            field.name for field in fields(application_type)
        ]


def test_launcher_owner_package_preserves_bootstrap_import_ceiling():
    forbidden = {"_application", "argparse", "fastmcp", "mcp", "pydantic"}
    imports = set()
    for path in sorted((CLI_DIR / "_launcher").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])

    assert imports.isdisjoint(forbidden)


@pytest.mark.parametrize(
    "request_factory",
    (
        lambda: BrainResolveRequest(""),
        lambda: BrainResolveRequest(" spaced "),
        lambda: BrainResolveRequest("Not-Canonical"),
        lambda: RuntimeResolveRequest(Path("relative")),
        lambda: RuntimeResolveRunnableRequest(Path("relative")),
    ),
)
def test_launcher_read_requests_reject_invalid_intent(request_factory):
    with pytest.raises(ValueError):
        request_factory()
