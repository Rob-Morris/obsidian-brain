"""Owner behaviour for portable skill, style, and plugin documents."""

from __future__ import annotations

import compile_router
import pytest

from _application.registry import current_request_resolver
from _application.resource.list import ListableResource, ResourceListRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _application.results import ErrorCode
from command_application import application_for


def test_skill_owners_return_exact_typed_documents(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    listing = application.invoke(
        ResourceListRequest(ListableResource.SKILL, query="software-design")
    )
    reading = application.invoke(
        ResourceReadRequest(ReadableResource.SKILL, "shaping")
    )

    assert listing.status == "ok"
    assert listing.result.total == 2
    assert [item.name for item in listing.result.items] == [
        "software-design-principles",
        "software-design-review",
    ]
    assert {item.source for item in listing.result.items} == {"core"}
    assert reading.status == "ok"
    assert reading.result.name == "shaping"
    assert reading.result.source == "core"
    assert "shaping" in reading.result.content.casefold()


def test_style_owners_return_sorted_names_and_content(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    listing = application.invoke(ResourceListRequest(ListableResource.STYLE))
    reading = application.invoke(
        ResourceReadRequest(ReadableResource.STYLE, "obsidian")
    )

    assert [item.name for item in listing.result.items] == ["obsidian", "writing"]
    assert listing.result.total == 2
    assert reading.result.name == "obsidian"
    assert reading.result.content.strip()


def test_plugin_owners_handle_empty_and_installed_collections(
    command_vault_baseline,
    command_vault_clone,
):
    baseline = application_for(command_vault_baseline.vault_root)
    assert baseline.invoke(ResourceListRequest(ListableResource.PLUGIN)).result.total == 0
    assert (
        baseline.invoke(
            ResourceReadRequest(ReadableResource.PLUGIN, "example")
        ).error.code
        is ErrorCode.NOT_FOUND
    )

    plugin_root = command_vault_clone.vault_root / "_Plugins" / "example"
    plugin_root.mkdir(parents=True)
    (plugin_root / "SKILL.md").write_text(
        "# Example plugin\n\nPortable plugin content.\n",
        encoding="utf-8",
    )
    router = compile_router.compile(str(command_vault_clone.vault_root))
    compile_router.persist_compiled_router(str(command_vault_clone.vault_root), router)
    application = application_for(command_vault_clone.vault_root)

    listing = application.invoke(ResourceListRequest(ListableResource.PLUGIN))
    reading = application.invoke(
        ResourceReadRequest(ReadableResource.PLUGIN, "example")
    )

    assert [item.name for item in listing.result.items] == ["example"]
    assert reading.result.name == "example"
    assert "Portable plugin content." in reading.result.content


def test_named_document_transport_resolves_only_its_exact_request_shape():
    resolver = current_request_resolver()

    skill = resolver.resolve(
        "resource.read", {"resource": "skill", "reference": "shaping"}
    )
    style = resolver.resolve("resource.list", {"resource": "style"})
    plugin = resolver.resolve(
        "resource.read", {"resource": "plugin", "reference": "example"}
    )
    assert type(skill) is ResourceReadRequest
    assert skill.resource is ReadableResource.SKILL
    assert type(style) is ResourceListRequest
    assert style.resource is ListableResource.STYLE
    assert type(plugin) is ResourceReadRequest
    assert plugin.resource is ReadableResource.PLUGIN


@pytest.mark.parametrize(
    ("command_id", "payload"),
    (
        ("resource.read", {"resource": "skill", "reference": ""}),
        ("resource.list", {"resource": "style", "query": ""}),
        (
            "resource.read",
            {"resource": "plugin", "reference": "example", "name": "other"},
        ),
    ),
)
def test_named_document_requests_reject_empty_or_extra_intent(command_id, payload):
    resolver = current_request_resolver()

    with pytest.raises(ValueError):
        resolver.resolve(command_id, payload)
