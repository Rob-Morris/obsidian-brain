"""Owner behaviour for exact artefact-type and template reads."""

from __future__ import annotations

import pytest

from _application.registry import current_request_resolver
from _application.resource.list import ListableResource, ResourceListRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _application.results import ErrorCode
from _application.type._classification import ArtefactTypeClassification
from command_application import application_for


def test_type_read_uses_the_exact_router_key_and_returns_the_definition(
    command_vault_baseline,
):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
        ResourceReadRequest(ReadableResource.TYPE, "designs")
    )
    singular_alias = application.invoke(
        ResourceReadRequest(ReadableResource.TYPE, "design")
    )

    assert result.status == "ok"
    assert result.result.key == "designs"
    assert result.result.classification is ArtefactTypeClassification.LIVING
    assert result.result.frontmatter_type == "living/design"
    assert result.result.taxonomy_path == "_Config/Taxonomy/Living/designs.md"
    assert result.result.template_path == "_Config/Templates/Living/Designs.md"
    assert result.result.content.startswith("# Designs\n")
    assert singular_alias.error.code is ErrorCode.NOT_FOUND


def test_type_list_is_bounded_sorted_and_filterable(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
        ResourceListRequest(ListableResource.TYPE, "living/design")
    )

    assert result.status == "ok"
    assert result.result.total == 1
    assert result.result.items[0].key == "designs"
    assert result.result.items[0].has_template is True


def test_template_read_and_list_use_the_same_exact_type_identity(
    command_vault_baseline,
):
    application = application_for(command_vault_baseline.vault_root)

    reading = application.invoke(
        ResourceReadRequest(ReadableResource.TEMPLATE, "designs")
    )
    singular_alias = application.invoke(
        ResourceReadRequest(ReadableResource.TEMPLATE, "design")
    )
    listing = application.invoke(
        ResourceListRequest(ListableResource.TEMPLATE, "design")
    )

    assert reading.status == "ok"
    assert reading.result.type_key == "designs"
    assert reading.result.artefact_type == "living/design"
    assert reading.result.path == "_Config/Templates/Living/Designs.md"
    assert reading.result.content.startswith("---\n")
    assert singular_alias.error.code is ErrorCode.NOT_FOUND
    assert [item.type_key for item in listing.result.items] == ["designs"]
    assert listing.result.items[0].path == "_Config/Templates/Living/Designs.md"


def test_type_and_template_transport_contracts_are_strict():
    resolver = current_request_resolver()

    assert type(
        resolver.resolve(
            "resource.read", {"resource": "type", "reference": "designs"}
        )
    ) is ResourceReadRequest
    assert type(
        resolver.resolve(
            "resource.list", {"resource": "type", "query": "design"}
        )
    ) is ResourceListRequest
    assert type(
        resolver.resolve(
            "resource.read", {"resource": "template", "reference": "designs"}
        )
    ) is ResourceReadRequest
    assert type(
        resolver.resolve("resource.list", {"resource": "template"})
    ) is ResourceListRequest

    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "resource.read",
            {"resource": "template", "reference": "designs", "name": "design"},
        )


def test_large_type_document_uses_generic_text_pager(command_vault_clone):
    from _application.projection import canonical_result_envelope
    from _application.result_navigation import build_view, body_fits
    from _common import document_revision

    root = command_vault_clone.vault_root
    path = root / "_Config/Taxonomy/Living/designs.md"
    text = "# Designs\n" + "Type policy 雪 and 🧠\n" * 2000
    path.write_text(text, encoding="utf-8")
    result = application_for(root).invoke(ResourceReadRequest(ReadableResource.TYPE, "designs"))
    assert result.status == "ok", result
    logical = canonical_result_envelope(result)
    assert logical["result"]["content"] == text
    assert "definition" not in logical["result"]
    assert "range" not in logical["result"]
    assert logical["result"]["revision"] == document_revision(path.read_bytes())
    view = build_view(logical, page_size=20)
    assert len(view.pages) > 1
    assert "".join(page["result"]["content"] for page in view.pages) == text
    metadata = {key: value for key, value in logical["result"].items() if key != "content"}
    position = 0
    for number, page in enumerate(view.pages, 1):
        payload = page["result"]
        assert body_fits(payload)
        assert "definition" not in payload and "fragments" not in payload
        assert {key: payload[key] for key in metadata} == metadata
        assert payload["content_range"] == {"start": position, "end": position + len(payload["content"]), "total": len(text)}
        assert page["delivery"]["page"] == number
        assert page["delivery"]["total_pages"] == len(view.pages)
        position += len(payload["content"])
    assert position == len(text)
