"""Owner behaviour for exact workspace reads, lists, and resolution."""

from __future__ import annotations

import json

import pytest

from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _application.workspace.list import WorkspaceListRequest
from _application.workspace.read import WorkspaceMode, WorkspaceReadRequest
from _application.workspace.resolve import WorkspaceResolveRequest
from command_application import application_for


def _add_embedded_workspace(clone, slug="analysis"):
    (clone.vault_root / "_Workspaces" / slug).mkdir(parents=True)
    hub = clone.vault_root / "Workspaces" / f"{slug}.md"
    hub.parent.mkdir(parents=True, exist_ok=True)
    hub.write_text(
        "---\n"
        "type: living/workspace\n"
        f"key: {slug}\n"
        "status: active\n"
        "workspace_mode: embedded\n"
        "tags:\n"
        f"  - workspace/{slug}\n"
        "---\n\n"
        f"# {slug.title()}\n",
        encoding="utf-8",
    )


def test_workspace_read_and_resolve_have_distinct_bounded_results(
    command_vault_clone,
):
    _add_embedded_workspace(command_vault_clone)
    application = application_for(command_vault_clone.vault_root)

    reading = application.invoke(WorkspaceReadRequest("analysis"))
    resolution = application.invoke(WorkspaceResolveRequest("analysis"))

    assert reading.status == "ok"
    assert reading.result.mode is WorkspaceMode.EMBEDDED
    assert reading.result.hub_path == "Workspaces/analysis.md"
    assert reading.result.status == "active"
    assert reading.result.tags == ("workspace/analysis",)
    assert resolution.result.slug == "analysis"
    assert resolution.result.path == str(
        command_vault_clone.vault_root / "_Workspaces" / "analysis"
    )


def test_workspace_list_is_sorted_and_embedded_wins_duplicate_registration(
    command_vault_clone,
):
    _add_embedded_workspace(command_vault_clone, "analysis")
    _add_embedded_workspace(command_vault_clone, "alpha")
    registry_path = command_vault_clone.vault_root / ".brain/local/workspaces.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "workspaces": {
                    "analysis": {"path": "/external/analysis"},
                    "remote": {"path": "/external/remote"},
                }
            }
        ),
        encoding="utf-8",
    )
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(WorkspaceListRequest())

    assert [item.slug for item in result.result.items] == [
        "alpha",
        "analysis",
        "remote",
    ]
    analysis = next(item for item in result.result.items if item.slug == "analysis")
    assert analysis.mode is WorkspaceMode.EMBEDDED


def test_workspace_commands_fail_closed_on_invalid_identity_and_registry(
    command_vault_clone,
):
    with pytest.raises(ValueError, match="slug must match"):
        WorkspaceReadRequest("../escape")

    registry_path = command_vault_clone.vault_root / ".brain/local/workspaces.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{broken", encoding="utf-8")
    application = application_for(command_vault_clone.vault_root)

    listing = application.invoke(WorkspaceListRequest())
    resolution = application.invoke(WorkspaceResolveRequest("missing"))

    assert listing.error.code is ErrorCode.CONFLICT
    assert resolution.error.code is ErrorCode.CONFLICT
    assert listing.effects == "none"


def test_workspace_transport_contracts_are_strict():
    resolver = current_request_resolver()

    assert type(resolver.resolve("workspace.list", {})) is WorkspaceListRequest
    assert type(
        resolver.resolve("workspace.read", {"reference": "analysis"})
    ) is WorkspaceReadRequest
    assert type(
        resolver.resolve("workspace.resolve", {"reference": "analysis"})
    ) is WorkspaceResolveRequest

    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve("workspace.list", {"query": "analysis"})
