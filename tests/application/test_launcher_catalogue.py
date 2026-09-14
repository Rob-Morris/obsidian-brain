"""Authoritative machine-global launcher catalogue contracts."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "cli" / "launcher_catalogue.py"
DISPOSITIONS = REPO_ROOT / "tests" / "fixtures" / "command_interface_dispositions_v1.json"


def _module():
    spec = importlib.util.spec_from_file_location("brain_launcher_catalogue", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _expected_launcher_commands():
    fixture = json.loads(DISPOSITIONS.read_text(encoding="utf-8"))
    historical = {
        command
        for group in fixture["command_contract_groups"].values()
        if group["contract"]["owner"] == "launcher"
        for command in group["commands"]
    }
    # The closed v1 audit remains evidence of its original surface. Epoch 3
    # replaces external approval with explicit CLI-only permission administration.
    return sorted((historical - {"access.approve"}) | {"permission.set-profile"})


def test_launcher_catalogue_is_complete_against_the_closed_disposition_evidence():
    catalogue = _module().LAUNCHER_CATALOGUE

    assert [entry.command_id for entry in catalogue.entries] == _expected_launcher_commands()
    assert len(catalogue.entries) == 24
    from _application.requests import (
        CommandDescribeRequest,
        CommandListRequest,
        InvocationReadRequest,
    )

    application_ids = {
        request_type.COMMAND_ID
        for request_type in (
            CommandDescribeRequest,
            CommandListRequest,
            InvocationReadRequest,
        )
    }
    assert application_ids.isdisjoint(entry.command_id for entry in catalogue.entries)


def test_launcher_entries_have_one_owner_and_explicit_projection_exclusions():
    catalogue = _module().LAUNCHER_CATALOGUE

    for entry in catalogue.entries:
        assert entry.owner == "launcher"
        assert entry.dependency_tier == "bootstrap"
        assert entry.locality == "machine_local"
        assert entry.owner_ref
        projections = {item.projection: item for item in entry.projections}
        assert projections["cli"].supported
        assert projections["launcher"].supported
        for name in ("mcp", "python", "script"):
            assert not projections[name].supported
            assert projections[name].reason


def test_launcher_catalogue_is_stdlib_only_and_does_not_import_application():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])

    assert imported <= {"__future__", "dataclasses", "hashlib", "json", "re"}
    assert "_application" not in MODULE_PATH.read_text(encoding="utf-8")


def test_launcher_fingerprint_is_deterministic_and_owner_collisions_fail():
    module = _module()
    catalogue = module.LAUNCHER_CATALOGUE
    assert catalogue.fingerprint == _module().LAUNCHER_CATALOGUE.fingerprint
    assert catalogue.fingerprint.startswith("sha256:")

    first, second = catalogue.entries[:2]
    duplicate_owner = module.LauncherEntry(
        second.command_id,
        second.command_version,
        first.owner_ref,
        second.entry_point,
        second.authority,
        second.effect_class,
        second.retry_class,
        second.required_providers,
    )
    with pytest.raises(ValueError, match="owner references"):
        module.LauncherCatalogue((first, duplicate_owner))
