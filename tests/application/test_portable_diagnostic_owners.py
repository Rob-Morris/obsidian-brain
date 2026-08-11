"""Owner behaviour for portable config, compliance, and type-status reads."""

from __future__ import annotations

import pytest

from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _application.runtime.read_environment import RuntimeReadEnvironmentRequest
from _application.type.status import TypeDefinitionState, TypeStatusRequest
from _application.vault.check import CheckSeverity, VaultCheckRequest
from _application.vault.read_config import VaultReadConfigRequest
from command_application import application_for


def test_runtime_read_environment_returns_bounded_sorted_facts(
    command_vault_baseline,
):
    result = application_for(command_vault_baseline.vault_root).invoke(
        RuntimeReadEnvironmentRequest()
    )

    assert result.status == "ok"
    facts = result.result.facts
    assert tuple(fact.name for fact in facts) == tuple(
        sorted(fact.name for fact in facts)
    )
    values = {fact.name: fact.value for fact in facts}
    assert values["vault_root"] == str(command_vault_baseline.vault_root)
    assert isinstance(values["platform"], str)
    assert all(
        isinstance(fact.value, (str, bool, int, float))
        or fact.value is None
        for fact in facts
    )


def test_vault_read_config_is_privacy_bounded(command_vault_baseline):
    result = application_for(command_vault_baseline.vault_root).invoke(
        VaultReadConfigRequest()
    )

    assert result.status == "ok"
    assert result.result.default_profile == "operator"
    assert result.result.profiles == (
        "administrator",
        "contributor",
        "maintainer",
        "operator",
        "reader",
    )
    assert result.result.semantic_retrieval is False
    assert not hasattr(result.result, "operators")
    assert not hasattr(result.result, "tool_paths")


def test_type_status_is_flat_typed_and_exact(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(TypeStatusRequest(("living/designs",)))
    missing = application.invoke(TypeStatusRequest(("living/not-a-type",)))

    assert result.status == "ok"
    assert result.result.total == 1
    assert result.result.items[0].type_key == "living/designs"
    assert result.result.items[0].state is TypeDefinitionState.IN_SYNC
    assert missing.error.code is ErrorCode.NOT_FOUND


def test_vault_check_returns_typed_filtered_findings(command_vault_clone):
    stray = command_vault_clone.vault_root / "stray.md"
    stray.write_text("# Stray\n", encoding="utf-8")
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        VaultCheckRequest(
            severity=CheckSeverity.ERROR,
            check="root_files",
            actionable=True,
        )
    )

    assert result.status == "ok"
    assert result.result.errors == 1
    assert result.result.warnings == 0
    assert result.result.findings[0].file == "stray.md"
    assert result.result.findings[0].fix
    assert result.result.findings[0].repair is None


def test_portable_diagnostic_transport_contracts_are_strict():
    resolver = current_request_resolver()

    assert type(
        resolver.resolve("runtime.read-environment", {})
    ) is RuntimeReadEnvironmentRequest
    assert type(resolver.resolve("vault.read-config", {})) is VaultReadConfigRequest
    assert type(
        resolver.resolve(
            "vault.check",
            {"severity": "warning", "actionable": True},
        )
    ) is VaultCheckRequest
    assert type(
        resolver.resolve("type.status", {"type_keys": ["living/designs"]})
    ) is TypeStatusRequest

    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve("vault.read-config", {"include_secrets": True})
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve("runtime.read-environment", {"verbose": True})
    with pytest.raises(ValueError, match="type_keys must be an array"):
        resolver.resolve("type.status", {"type_keys": "living/designs"})
