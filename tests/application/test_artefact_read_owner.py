"""Owner-level behaviour for portable artefact reads, lists and discovery."""

from __future__ import annotations

from _application.artefact.list import ArtefactListRequest, ArtefactSort
from _application.artefact.outline import ArtefactOutlineRequest
from _application.artefact.read import ArtefactReadRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.requests import CommandListRequest
from _application.results import ErrorCode
from _application.types import Projection
from command_application import application_for


FINAL_COMMAND_IDS = (
    "access.reduce",
    "access.request",
    "access.status",
    "artefact.archive",
    "artefact.convert",
    "artefact.create",
    "artefact.delete",
    "artefact.list",
    "artefact.migrate-naming",
    "artefact.outline",
    "artefact.read",
    "artefact.rename",
    "artefact.repair",
    "artefact.reparent",
    "artefact.reparent-children",
    "artefact.search",
    "artefact.set-key",
    "artefact.set-naming-field",
    "artefact.set-status",
    "artefact.unarchive",
    "attachment.upload",
    "command.describe",
    "command.list",
    "content.classify",
    "content.ingest",
    "content.resolve",
    "document.edit",
    "document.patch",
    "document.update-frontmatter",
    "document.write",
    "invocation.read",
    "links.check",
    "links.fix",
    "plugin.create",
    "plugin.replace",
    "resource.create",
    "resource.list",
    "resource.read",
    "resource.search",
    "retrieval.construct-benchmark",
    "retrieval.enable",
    "retrieval.evaluate",
    "retrieval.rebuild-semantic",
    "retrieval.refresh-lexical",
    "retrieval.repair-semantic",
    "runtime.read-environment",
    "runtime.refresh-router",
    "runtime.status",
    "runtime.warmup",
    "session.start",
    "shaping.render",
    "shaping.start",
    "stage.create",
    "stage.discard",
    "trigger.create",
    "trigger.delete",
    "trigger.replace",
    "type.create",
    "type.replace",
    "type.status",
    "type.sync",
    "vault.check",
    "vault.read-config",
    "vault.read-file",
    "vault.read-router",
    "workspace.bind",
    "workspace.configure-bootstrap",
    "workspace.list",
    "workspace.read",
    "workspace.register",
    "workspace.repair-registry",
    "workspace.setup",
    "workspace.unregister",
    "workspace.update-metadata",
)


def test_artefact_read_uses_the_installed_portable_owner(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        ArtefactReadRequest("project/command-fixture")
    )

    assert result.status == "ok"
    assert result.result.reference == "project/command-fixture"
    assert result.result.location.value == "active"
    assert "# Command Fixture" in result.result.content


def test_artefact_read_maps_missing_and_escape_errors_before_effects(
    command_vault_baseline,
):
    application = application_for(command_vault_baseline.vault_root)
    missing = application.invoke(ArtefactReadRequest("Ideas/Does Not Exist.md"))
    escaped = application.invoke(ArtefactReadRequest("../outside.md"))

    assert missing.error.code is ErrorCode.NOT_FOUND
    assert missing.effects == "none"
    assert escaped.error.code is ErrorCode.INVALID_REQUEST
    assert escaped.effects == "none"


def test_catalogue_identity_is_the_exact_final_74_command_surface():
    resolver = current_request_resolver()
    request = resolver.resolve(
        "artefact.read",
        {"reference": "project/command-fixture"},
    )
    catalogue = current_application_catalogue()

    assert type(request) is ArtefactReadRequest
    assert catalogue.resolve(request).command_id == "artefact.read"
    assert tuple(entry.command_id for entry in catalogue.entries) == FINAL_COMMAND_IDS


def test_physical_admin_commands_are_explicitly_excluded_only_from_mcp():
    catalogue = current_application_catalogue()
    excluded = {
        "artefact.migrate-naming",
        "retrieval.enable",
        "workspace.repair-registry",
    }

    for command_id in excluded:
        entry = next(item for item in catalogue.entries if item.command_id == command_id)
        assert Projection.MCP not in entry.eligible_projections
        assert set(entry.eligible_projections) == {
            Projection.CLI,
            Projection.SCRIPT,
            Projection.PYTHON,
        }
        assert entry.projections[0].reason


def test_foundational_discovery_immediately_includes_cohesive_artefact_owners(
    tmp_path,
):
    result = application_for(tmp_path).invoke(CommandListRequest(domain="artefact"))

    assert result.result.command_ids == (
        "artefact.archive",
        "artefact.convert",
        "artefact.create",
        "artefact.delete",
        "artefact.list",
        "artefact.migrate-naming",
        "artefact.outline",
        "artefact.read",
        "artefact.rename",
        "artefact.repair",
        "artefact.reparent",
        "artefact.reparent-children",
        "artefact.search",
        "artefact.set-key",
        "artefact.set-naming-field",
        "artefact.set-status",
        "artefact.unarchive",
    )


def test_artefact_outline_uses_the_same_structural_scanner(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        ArtefactOutlineRequest("design/command-fixture-design")
    )
    repeated = [
        target
        for target in result.result.targets
        if target.target.endswith("Repeated Target")
    ]

    assert result.status == "ok"
    assert len(repeated) == 2
    assert [target.occurrence for target in repeated] == [1, 2]
    assert all(target.line > 0 for target in result.result.targets)


def test_artefact_outline_transport_resolves_the_same_typed_request():
    request = current_request_resolver().resolve(
        "artefact.outline",
        {"reference": "design/command-fixture-design"},
    )
    assert type(request) is ArtefactOutlineRequest


def test_artefact_list_returns_typed_stable_pages(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)
    first = application.invoke(ArtefactListRequest(page_size=2, sort=ArtefactSort.TITLE))
    second = application.invoke(
        ArtefactListRequest(
            page_size=2,
            sort=ArtefactSort.TITLE,
            cursor=first.result.next_cursor,
        )
    )
    project = application.invoke(ArtefactListRequest(type_filter="project"))

    assert first.result.returned == 2
    assert first.result.truncated is True
    assert second.result.items != first.result.items
    assert project.result.returned == 1
    assert project.result.items[0].reference == "project/command-fixture"
    assert project.result.items[0].frontmatter_key == "command-fixture"


def test_artefact_list_invalid_filters_are_structural_errors(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        ArtefactListRequest(since="not-a-date")
    )
    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"
