"""Owner-level behaviour for the migrated portable ``artefact.read`` command."""

from __future__ import annotations

from _application.artefact.list import ArtefactListRequest, ArtefactSort
from _application.artefact.outline import ArtefactOutlineRequest
from _application.artefact.read import ArtefactReadRequest
from _application.links.check import LinksCheckRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.requests import CommandListRequest
from _application.results import ErrorCode
from _application.runtime.read_environment import RuntimeReadEnvironmentRequest
from _application.vault.read_router import VaultReadRouterRequest
from command_application import application_for


def test_artefact_read_uses_the_installed_portable_owner(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(ArtefactReadRequest("project/command-fixture"))

    assert result.status == "ok"
    assert result.result.reference == "project/command-fixture"
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


def test_artefact_read_transport_and_catalogue_identity_are_one_to_one():
    resolver = current_request_resolver()
    request = resolver.resolve(
        "artefact.read",
        {"reference": "project/command-fixture"},
    )
    catalogue = current_application_catalogue()

    assert type(request) is ArtefactReadRequest
    assert catalogue.resolve(request).command_id == "artefact.read"
    assert [entry.command_id for entry in catalogue.entries] == [
        "artefact.append",
        "artefact.archive",
        "artefact.convert",
        "artefact.create",
        "artefact.delete",
        "artefact.delete-section",
        "artefact.edit",
        "artefact.list",
        "artefact.list-archived",
        "artefact.migrate-naming",
        "artefact.outline",
        "artefact.prepend",
        "artefact.read",
        "artefact.read-archived",
        "artefact.rename",
        "artefact.repair-frontmatter",
        "artefact.repair-ownership",
        "artefact.reparent",
        "artefact.reparent-children",
        "artefact.replace-text",
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
        "invocation.read",
        "links.check",
        "links.fix",
        "memory.append",
        "memory.create",
        "memory.delete-section",
        "memory.edit",
        "memory.list",
        "memory.prepend",
        "memory.read",
        "memory.replace-text",
        "memory.search",
        "plugin.create",
        "plugin.list",
        "plugin.read",
        "plugin.replace",
        "plugin.search",
        "retrieval.construct-benchmark",
        "retrieval.enable",
        "retrieval.evaluate",
        "retrieval.rebuild-lexical",
        "retrieval.rebuild-semantic",
        "retrieval.repair-lexical",
        "retrieval.repair-semantic",
        "runtime.read-environment",
        "runtime.rebuild-router",
        "runtime.repair-router",
        "session.start",
        "shaping.render-presentation",
        "shaping.render-printable",
        "shaping.start",
        "skill.append",
        "skill.create",
        "skill.delete-section",
        "skill.edit",
        "skill.list",
        "skill.prepend",
        "skill.read",
        "skill.replace-text",
        "skill.search",
        "stage.create",
        "stage.discard",
        "style.append",
        "style.create",
        "style.delete-section",
        "style.edit",
        "style.list",
        "style.prepend",
        "style.read",
        "style.replace-text",
        "style.search",
        "template.append",
        "template.create",
        "template.delete-section",
        "template.edit",
        "template.list",
        "template.prepend",
        "template.read",
        "template.replace-text",
        "trigger.create",
        "trigger.delete",
        "trigger.list",
        "trigger.read",
        "trigger.replace",
        "trigger.search",
        "type.create",
        "type.install",
        "type.list",
        "type.read",
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
        "workspace.resolve",
        "workspace.setup",
        "workspace.unregister",
        "workspace.update-metadata",
    ]


def test_foundational_discovery_immediately_includes_migrated_owner(tmp_path):
    application = application_for(tmp_path)

    result = application.invoke(CommandListRequest(domain="artefact"))

    assert result.result.command_ids == (
        "artefact.append",
        "artefact.archive",
        "artefact.convert",
        "artefact.create",
        "artefact.delete",
        "artefact.delete-section",
        "artefact.edit",
        "artefact.list",
        "artefact.list-archived",
        "artefact.migrate-naming",
        "artefact.outline",
        "artefact.prepend",
        "artefact.read",
        "artefact.read-archived",
        "artefact.rename",
        "artefact.repair-frontmatter",
        "artefact.repair-ownership",
        "artefact.reparent",
        "artefact.reparent-children",
        "artefact.replace-text",
        "artefact.search",
        "artefact.set-key",
        "artefact.set-naming-field",
        "artefact.set-status",
        "artefact.unarchive",
    )


def test_artefact_outline_uses_the_same_structural_scanner(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
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


def test_artefact_list_transport_resolves_sort_and_pagination():
    request = current_request_resolver().resolve(
        "artefact.list",
        {"sort": "modified_desc", "page_size": 25},
    )

    assert type(request) is ArtefactListRequest
    assert request.sort is ArtefactSort.MODIFIED_DESC
    assert request.page_size == 25


def test_runtime_environment_is_a_typed_scalar_view(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        RuntimeReadEnvironmentRequest()
    )
    facts = {fact.name: fact.value for fact in result.result.facts}

    assert result.status == "ok"
    assert facts["vault_root"] == str(command_vault_baseline.vault_root)
    assert isinstance(facts["platform"], str)
    assert isinstance(facts["cli_available"], bool)


def test_router_metadata_is_typed_without_an_unbounded_metadata_bag(
    command_vault_baseline,
):
    result = application_for(command_vault_baseline.vault_root).invoke(
        VaultReadRouterRequest()
    )

    assert result.status == "ok"
    assert result.result.brain_core_version == "0.54.57"
    assert result.result.always_rules
    assert result.result.source_hash.startswith("sha256:")
    assert len(result.result.sources) > 0


def test_links_check_returns_typed_findings_without_router_probe(
    command_vault_baseline,
):
    result = application_for(command_vault_baseline.vault_root).invoke(
        LinksCheckRequest()
    )

    assert result.status == "ok"
    assert result.result.warnings == sum(
        finding.severity == "warning" for finding in result.result.findings
    )
    assert result.result.info == sum(
        finding.severity == "info" for finding in result.result.findings
    )


def test_zero_input_read_owners_reject_transport_extras():
    resolver = current_request_resolver()
    assert type(resolver.resolve("links.check", {})) is LinksCheckRequest
    assert (
        type(resolver.resolve("runtime.read-environment", {}))
        is RuntimeReadEnvironmentRequest
    )
    assert (
        type(resolver.resolve("vault.read-router", {}))
        is VaultReadRouterRequest
    )
