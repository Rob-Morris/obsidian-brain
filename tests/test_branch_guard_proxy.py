"""Owned response-loss recovery validates bounded diagnostics without replay."""

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from brain_mcp import proxy
from brain_mcp._interface_protocol import AcceptedCallRecord
from _bootstrap.branch_guard_details import BranchGuardDetails, GuardRecoveryLocation
from _command_interface.receipts import _decode_branch_guard


@pytest.fixture
def lookup():
    record = AcceptedCallRecord(7, {"params": {"arguments": {}}}, "artefact_create",
        "artefact.create", 1, 1, "sha256:" + "a" * 64, "mutation", "mcp-example",
        datetime(2026, 10, 29, tzinfo=timezone.utc))
    identity = {"reference": {"invocation_id": record.invocation_id},
                "command_id": record.command_id, "command_version": record.command_version}
    outcome = {"receipt": {**identity, "state": "known_partial",
        "recorded_at": "2026-10-29T00:01:00+00:00",
        "committed_effects": [{"kind": "artefact.created", "subject": "Notes/example.md"}]},
        "execution": "partial", "config_revision": None}
    payload = {"reference": identity["reference"], "state": "found",
        "intent": {**identity, "recorded_at": "2026-10-29T00:00:00+00:00",
            "basis": "initial", "generation": "generation", "source": "host-request",
            "grant_id": None, "operation_id": None, "operation_digest": None,
            "request_id": None, "permission_change": None}, "outcome": outcome}
    response = {"jsonrpc": "2.0", "id": "query", "result": {"isError": False,
        "structuredContent": {"schema": "brain.command-result/1", "command": "invocation.read",
            "command_version": 4, "status": "ok", "warnings": [], "committed_effects": [],
            "result": payload}}}
    return record, response, outcome


def guard():
    # JSON wire arrays, including the nested location array.
    import json
    return json.loads(json.dumps(asdict(BranchGuardDetails("branch-changed", "/vault",
        "/repository", "main", ("dev",), ("Notes/example.md",),
        (GuardRecoveryLocation("journal_path", "/vault/.brain/local/journal"),)))))


def parse(lookup, version=4):
    record, response, _ = lookup
    response["result"]["structuredContent"]["command_version"] = version
    return proxy._parse_receipt_lookup_response(response, query_id="query", record=record,
                                               invocation_read_version=version)


@pytest.mark.parametrize("version", [3, 4])
def test_base_receipt_remains_recoverable(lookup, version):
    assert parse(lookup, version) is lookup[2]


@pytest.mark.parametrize("details", [None, guard()])
def test_v4_recovers_and_returns_guard_metadata_intact(lookup, details):
    record, _, outcome = lookup
    outcome["receipt"]["branch_guard"] = details
    assert parse(lookup) is outcome
    recovered = proxy._resolved_outcome_response(record, outcome)["result"]
    assert recovered["structuredContent"]["outcome"]["receipt"]["branch_guard"] == details
    assert recovered["structuredContent"]["retryable"] is False
    assert recovered["isError"] is True
    assert _decode_branch_guard(details) == (None if details is None else BranchGuardDetails(
        "branch-changed", "/vault", "/repository", "main", ("dev",), ("Notes/example.md",),
        (GuardRecoveryLocation("journal_path", "/vault/.brain/local/journal"),)))


@pytest.mark.parametrize("details", [None, guard()])
def test_v3_requires_exact_legacy_receipt(lookup, details):
    lookup[2]["receipt"]["branch_guard"] = details
    with pytest.raises(ValueError, match="receipt shape"):
        parse(lookup, 3)


@pytest.mark.parametrize("patch", [
    {"extra": True}, {"reason": None}, {"reason": ""}, {"reason": "x" * 2049},
    {"vault": 1}, {"repository": []}, {"observed_branch": False},
    {"allowed_branches": "dev"}, {"allowed_branches": [None]},
    {"allowed_branches": ["dev"] * 65}, {"allowed_branches": ["x" * 2049]},
    {"committed_paths": [None]}, {"committed_paths": ["p"] * 65},
    {"committed_paths": ["x" * 2049]}, {"recovery_locations": [None]},
    {"recovery_locations": [{"kind": "journal_path"}]},
    {"recovery_locations": [{"kind": "", "path": "p"}]},
    {"recovery_locations": [{"kind": "journal_path", "path": None}]},
    {"recovery_locations": [{"kind": "journal_path", "path": "x" * 2049}]},
    {"recovery_locations": [{"kind": "journal_path", "path": "p"}] * 65},
    {"truncated": 1}, {"inspection_command": "artefact.create"},
])
def test_malformed_guard_rejected_by_both_readers(lookup, patch):
    details = guard() | patch
    lookup[2]["receipt"]["branch_guard"] = details
    with pytest.raises(ValueError):
        parse(lookup)
    with pytest.raises(ValueError):
        _decode_branch_guard(details)


@pytest.mark.parametrize("field,value", [
    ("unrelated", None), ("command_id", "artefact.delete"),
    ("reference", {"invocation_id": "other"}), ("command_version", True),
    ("committed_effects", []), ("recorded_at", "2026-10-28T00:00:00+00:00"),
])
def test_guard_does_not_relax_owned_receipt_checks(lookup, field, value):
    lookup[2]["receipt"].update(branch_guard=guard(), **{field: value})
    with pytest.raises(ValueError):
        parse(lookup)


def test_public_model_imports_remain_identical():
    from _application.types import BranchGuardDetails as PublicDetails
    from _application.types import GuardRecoveryLocation as PublicLocation
    assert PublicDetails is BranchGuardDetails
    assert PublicLocation is GuardRecoveryLocation


@pytest.mark.parametrize("value", [False, [], "branch-changed", {}, {"reason": "branch-changed"}])
def test_invalid_guard_shape_rejected(lookup, value):
    lookup[2]["receipt"]["branch_guard"] = value
    with pytest.raises(ValueError):
        parse(lookup)
    with pytest.raises(ValueError):
        _decode_branch_guard(value)


def test_v4_accepts_exact_diagnostic_bounds(lookup):
    details = guard() | {"reason": "x" * 2048, "vault": None, "repository": None,
        "observed_branch": None, "allowed_branches": ["x" * 2048] * 64,
        "committed_paths": ["p" * 2048] * 64,
        "recovery_locations": [{"kind": "k" * 2048, "path": "p" * 2048}] * 64,
        "truncated": True}
    lookup[2]["receipt"]["branch_guard"] = details
    assert parse(lookup)["receipt"]["branch_guard"] == details


def test_diagnostic_leaf_imports_only_stdlib():
    import ast
    from pathlib import Path
    import sys
    from _bootstrap import branch_guard_details
    tree = ast.parse(Path(branch_guard_details.__file__).read_text())
    imports = {alias.name.split(".")[0] for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names}
    imports.update(node.module.split(".")[0] for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module)
    assert imports <= set(sys.stdlib_module_names)
