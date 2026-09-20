"""Launcher-owned discovery and outer local-CLI composition contracts."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _local_cli.discovery import (
    ApplicationDiscoveryPage,
    compose_application_description,
    compose_launcher_description,
    compose_list,
)
from _launcher.context import ProviderBindings
from _launcher.discovery import LauncherCursor, describe_command, list_commands
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.projection import (
    minimal_request_payload,
    request_schema,
    resolve_request,
    result_schema,
)


CLI_DIR = Path(__file__).resolve().parents[2] / "cli"


class _Provider:
    def __init__(self, provider_id: str, available: bool):
        self.provider_id = provider_id
        self.available = available


def _application_page(command_id: str = "artefact.read") -> ApplicationDiscoveryPage:
    return ApplicationDiscoveryPage(
        "brain.command-catalogue/1",
        "sha256:application",
        (
            {
                "command_id": command_id,
                "command_version": 1,
                "summary": "Read one artefact.",
                "owner": "application",
            },
        ),
        None,
    )


def test_every_launcher_entry_has_authoritative_summary_and_discovery_contract():
    page = list_commands()

    assert page.schema == "brain.launcher-catalogue/1"
    assert page.catalogue_fingerprint == LAUNCHER_CATALOGUE.fingerprint
    assert len(page.entries) == len(LAUNCHER_CATALOGUE.entries) == 27
    assert tuple(item.command_id for item in page.entries) == tuple(
        item.command_id for item in LAUNCHER_CATALOGUE.entries
    )
    for entry, summary in zip(LAUNCHER_CATALOGUE.entries, page.entries, strict=True):
        assert entry.summary == summary.summary
        assert 1 <= len(entry.summary.removesuffix(".").split()) <= 12
        assert summary.owner == "launcher"
        assert summary.owner_ref == entry.owner_ref
        assert summary.availability == (
            "unavailable" if entry.required_providers else "available"
        )
        assert summary.availability_freshness == "fresh"


def test_launcher_list_filters_and_paginates_against_static_manifest():
    providers = ProviderBindings((_Provider("caller_filesystem", True),))
    first = list_commands(
        providers=providers,
        domain="brain",
        authority="reader",
        dependency_tier="bootstrap",
        locality="machine_local",
        effect_class="none",
        retry_class="safe",
        page_size=2,
    )

    assert tuple(item.command_id for item in first.entries) == (
        "brain.doctor",
        "brain.get-default",
    )
    assert first.next_cursor == LauncherCursor(
        LAUNCHER_CATALOGUE.fingerprint,
        "brain.get-default",
    )
    second = list_commands(
        providers=providers,
        domain="brain",
        authority="reader",
        dependency_tier="bootstrap",
        locality="machine_local",
        effect_class="none",
        retry_class="safe",
        cursor=first.next_cursor,
        page_size=2,
    )
    assert tuple(item.command_id for item in second.entries) == (
        "brain.list",
        "brain.resolve",
    )
    assert tuple(
        item.command_id
        for item in list_commands(query="generate key").entries
    ) == ("operator.generate-key",)
    assert len(list_commands(projection="cli").entries) == 27
    assert not list_commands(projection="mcp").entries

    with pytest.raises(ValueError, match="fingerprint"):
        list_commands(
            cursor=LauncherCursor("sha256:stale", "brain.get-default")
        )
    with pytest.raises(ValueError, match="canonical noun"):
        list_commands(domain="Brain")
    with pytest.raises(ValueError, match="projection is invalid"):
        list_commands(projection="made-up")


def test_launcher_availability_uses_only_already_bound_provider_state():
    unavailable = list_commands(
        availability="unavailable",
        providers=ProviderBindings((_Provider("caller_filesystem", False),)),
    )
    available = list_commands(
        availability="available",
        providers=ProviderBindings((_Provider("caller_filesystem", True),)),
    )

    assert unavailable.entries
    assert all(item.missing_providers == ("caller_filesystem",) for item in unavailable.entries)
    assert all(item.availability == "available" for item in available.entries)
    assert len(available.entries) == 27


def test_every_launcher_description_derives_from_owning_request_and_result_types():
    owners = {owner.command_id: owner for owner in LAUNCHER_OWNERS.entries}
    for entry in LAUNCHER_CATALOGUE.entries:
        owner = owners[entry.command_id]
        description = describe_command(entry.command_id)
        request = json.loads(description.request_schema_json)
        result = json.loads(description.result_schema_json)
        example = json.loads(description.example_json)

        assert request == request_schema(owner.request_type)
        assert result == result_schema(owner.result_type)
        assert type(resolve_request(owner.request_type, example)) is owner.request_type
        assert example == minimal_request_payload(owner.request_type)
        assert description.owner_ref == entry.owner_ref
        assert description.result_type.endswith(owner.result_type.__qualname__)
        assert description.result_variants[0][0] == "ok"
        assert "invalid_request" in description.error_codes
        assert "degraded_capability" in description.warning_codes

    with pytest.raises(KeyError, match="not installed"):
        describe_command("unknown.command")


def test_launcher_dynamic_resolver_rejects_unknown_and_wrong_typed_fields():
    owner = next(
        item for item in LAUNCHER_OWNERS.entries if item.command_id == "brain.resolve"
    )

    with pytest.raises(ValueError, match="unknown fields"):
        resolve_request(owner.request_type, {"brain_id": "example-brain", "extra": True})
    with pytest.raises(ValueError, match="expected a string"):
        resolve_request(owner.request_type, {"brain_id": 3})


def test_outer_cli_view_preserves_owner_provenance_and_rejects_collisions():
    application = _application_page()
    launcher = list_commands(
        providers=ProviderBindings((_Provider("caller_filesystem", True),)),
        query="generate key",
    )
    composed = compose_list(owner="all", application=application, launcher=launcher)

    assert [(entry.command_id, entry.owner) for entry in composed.entries] == [
        ("artefact.read", "application"),
        ("operator.generate-key", "launcher"),
    ]
    assert composed.entries[0].catalogue_fingerprint == "sha256:application"
    assert composed.entries[1].catalogue_fingerprint == LAUNCHER_CATALOGUE.fingerprint
    assert compose_list(owner="application", application=application).entries[0].owner == "application"
    assert compose_list(owner="launcher", launcher=launcher).entries[0].owner == "launcher"

    collision = replace(
        application,
        entries=(
            {
                "command_id": "operator.generate-key",
                "command_version": 1,
                "summary": "Collision.",
            },
        ),
    )
    with pytest.raises(ValueError, match="collide"):
        compose_list(owner="all", application=collision, launcher=launcher)


def test_outer_cli_descriptions_retain_the_original_owned_payload():
    application_payload = {
        "command_id": "artefact.read",
        "command_version": 1,
        "summary": "Read one artefact.",
        "request_schema_json": "{}",
    }
    application = compose_application_description(
        catalogue_schema="brain.command-catalogue/1",
        catalogue_fingerprint="sha256:application",
        payload=application_payload,
    )
    launcher_description = describe_command("operator.generate-key")
    launcher = compose_launcher_description(
        launcher_description,
        catalogue_schema=LAUNCHER_CATALOGUE.schema,
        catalogue_fingerprint=LAUNCHER_CATALOGUE.fingerprint,
    )

    assert application.payload is application_payload
    assert application.owner == "application"
    assert launcher.owner == "launcher"
    assert launcher.payload["owner_ref"] == "_launcher.operator:generate_key"

    with pytest.raises(ValueError, match="catalogue schema"):
        compose_application_description(
            catalogue_schema="brain.launcher-catalogue/1",
            catalogue_fingerprint="sha256:application",
            payload=application_payload,
        )


def test_outer_cli_package_is_distinct_and_does_not_import_application_semantics():
    assert not tuple((CLI_DIR / "_command_interface").glob("*.py"))
    imports = set()
    for path in sorted((CLI_DIR / "_local_cli").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])

    assert "_application" not in imports
    assert "_command_interface" not in imports


@pytest.mark.parametrize("owner", ("application", "launcher", "all"))
def test_outer_cli_view_requires_each_requested_owner_source(owner):
    with pytest.raises(ValueError, match="not supplied"):
        compose_list(owner=owner)
