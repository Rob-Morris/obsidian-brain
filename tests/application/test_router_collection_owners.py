"""Owner behaviour for exact memory and trigger router collections."""

from __future__ import annotations

import json

import pytest

from _application.registry import current_request_resolver
from _application.resource.list import ListableResource, ResourceListRequest
from _application.resource.read import ReadableResource, ResourceReadRequest
from _application.results import ErrorCode
from _application.trigger.read import TriggerCategory
from command_application import application_for


def test_memory_read_is_exact_while_discovery_remains_separate(
    command_vault_baseline,
):
    application = application_for(command_vault_baseline.vault_root)

    exact = application.invoke(
        ResourceReadRequest(ReadableResource.MEMORY, "brain-core-reference")
    )
    trigger_phrase = application.invoke(
        ResourceReadRequest(ReadableResource.MEMORY, "brain core")
    )

    assert exact.status == "ok"
    assert exact.result.name == "brain-core-reference"
    assert "brain core" in exact.result.triggers
    assert exact.result.content.strip()
    assert trigger_phrase.error.code is ErrorCode.NOT_FOUND


def test_memory_list_returns_bounded_trigger_metadata(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        ResourceListRequest(ListableResource.MEMORY, query="brain-core")
    )

    assert result.status == "ok"
    assert result.result.total == 1
    assert result.result.items[0].name == "brain-core-reference"
    assert result.result.items[0].triggers == (
        "brain core",
        "obsidian-brain",
        "brain system",
        "vault system",
    )


def test_trigger_read_uses_unique_canonical_condition(command_vault_baseline):
    target = "_Config/Taxonomy/Temporal/logs"
    result = application_for(command_vault_baseline.vault_root).invoke(
        ResourceReadRequest(ReadableResource.TRIGGER, "After meaningful work")
    )

    assert result.status == "ok"
    assert result.result.target == target
    assert result.result.category is TriggerCategory.AFTER
    assert result.result.condition == "After meaningful work"


def test_trigger_list_query_searches_bounded_trigger_fields(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        ResourceListRequest(ListableResource.TRIGGER, query="meaningful work")
    )

    assert result.status == "ok"
    assert [item.target for item in result.result.items] == [
        "_Config/Taxonomy/Temporal/logs"
    ]


def test_trigger_read_rejects_duplicate_condition_state(command_vault_clone):
    router_path = (
        command_vault_clone.vault_root / ".brain" / "local" / "compiled-router.json"
    )
    router = json.loads(router_path.read_text(encoding="utf-8"))
    original = next(
        trigger
        for trigger in router["triggers"]
        if trigger["condition"] == "After meaningful work"
    )
    router["triggers"].append({**original, "target": "_Config/router"})
    router_path.write_text(json.dumps(router), encoding="utf-8")

    result = application_for(command_vault_clone.vault_root).invoke(
        ResourceReadRequest(ReadableResource.TRIGGER, "After meaningful work")
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"


def test_router_collection_transport_contracts_are_strict():
    resolver = current_request_resolver()

    assert type(
        resolver.resolve(
            "resource.read",
            {"resource": "memory", "reference": "brain-core-reference"},
        )
    ) is ResourceReadRequest
    assert type(
        resolver.resolve("resource.list", {"resource": "memory"})
    ) is ResourceListRequest
    assert type(
        resolver.resolve(
            "resource.read",
            {"resource": "trigger", "reference": "After meaningful work"},
        )
    ) is ResourceReadRequest
    assert type(
        resolver.resolve(
            "resource.list", {"resource": "trigger", "query": "log"}
        )
    ) is ResourceListRequest

    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "resource.read",
            {
                "resource": "trigger",
                "reference": "After meaningful work",
                "name": "logs",
            },
        )
