"""Owner behaviour for portable skill, style, and plugin documents."""

from __future__ import annotations

import compile_router
import pytest

from _application.plugin.list import PluginListRequest
from _application.plugin.read import PluginReadRequest
from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _application.skill.list import SkillListRequest
from _application.skill.read import SkillReadRequest
from _application.style.list import StyleListRequest
from _application.style.read import StyleReadRequest
from command_application import application_for


def test_skill_owners_return_exact_typed_documents(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    listing = application.invoke(SkillListRequest(query="software-design"))
    reading = application.invoke(SkillReadRequest("shaping"))

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

    listing = application.invoke(StyleListRequest())
    reading = application.invoke(StyleReadRequest("obsidian"))

    assert [item.name for item in listing.result.items] == ["obsidian", "writing"]
    assert listing.result.total == 2
    assert reading.result.name == "obsidian"
    assert reading.result.content.strip()


def test_plugin_owners_handle_empty_and_installed_collections(
    command_vault_baseline,
    command_vault_clone,
):
    baseline = application_for(command_vault_baseline.vault_root)
    assert baseline.invoke(PluginListRequest()).result.total == 0
    assert (
        baseline.invoke(PluginReadRequest("example")).error.code
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

    listing = application.invoke(PluginListRequest())
    reading = application.invoke(PluginReadRequest("example"))

    assert [item.name for item in listing.result.items] == ["example"]
    assert reading.result.name == "example"
    assert "Portable plugin content." in reading.result.content


def test_named_document_transport_resolves_only_its_exact_request_shape():
    resolver = current_request_resolver()

    assert type(resolver.resolve("skill.read", {"reference": "shaping"})) is SkillReadRequest
    assert type(resolver.resolve("skill.list", {"query": "shape"})) is SkillListRequest
    assert type(resolver.resolve("style.read", {"reference": "obsidian"})) is StyleReadRequest
    assert type(resolver.resolve("style.list", {})) is StyleListRequest
    assert type(resolver.resolve("plugin.read", {"reference": "example"})) is PluginReadRequest
    assert type(resolver.resolve("plugin.list", {})) is PluginListRequest


@pytest.mark.parametrize(
    ("command_id", "payload"),
    (
        ("skill.read", {"reference": ""}),
        ("style.list", {"query": ""}),
        ("plugin.read", {"reference": "example", "name": "other"}),
    ),
)
def test_named_document_requests_reject_empty_or_extra_intent(command_id, payload):
    resolver = current_request_resolver()

    with pytest.raises(ValueError):
        resolver.resolve(command_id, payload)
