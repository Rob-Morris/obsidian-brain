"""Owner behaviour for managed semantic retrieval commands."""

from __future__ import annotations

import pytest

from _application._semantic_maintenance import SemanticMaintenanceStatus
from _application.context import Capability
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.retrieval.enable import RetrievalEnableRequest
from _application.retrieval.rebuild_semantic import RetrievalRebuildSemanticRequest
from _application.retrieval.repair_semantic import RetrievalRepairSemanticRequest
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    RetryClass,
)
from command_application import application_for
import _lifecycle.fresh_interpreter as fresh_interpreter


class _SemanticProvider:
    provider_id = "semantic_runtime"


def _managed_application(root, *, dry_run=False):
    return application_for(
        root,
        dependency_tier=DependencyTier.MANAGED,
        providers=(_SemanticProvider(),),
        capabilities=(Capability("semantic_runtime", Availability.AVAILABLE),),
        dry_run=dry_run,
    )


def test_semantic_enable_dry_run_plans_without_writing_config(command_vault_clone):
    root = command_vault_clone.vault_root
    config_path = root / ".brain/local/config.yaml"
    before = config_path.read_bytes() if config_path.exists() else None

    result = _managed_application(root, dry_run=True).invoke(RetrievalEnableRequest())

    assert result.status == "ok"
    assert result.result.status is SemanticMaintenanceStatus.PLANNED
    assert result.result.operation == "enable"
    assert result.result.dry_run is True
    assert tuple(step.name for step in result.result.steps) == (
        "semantic_config",
        "semantic_runtime",
        "semantic_model",
        "semantic_assets",
        "semantic_runtime_marker",
    )
    assert result.committed_effects == ()
    assert (config_path.read_bytes() if config_path.exists() else None) == before


def test_semantic_enable_reports_committed_config_and_assets(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root

    monkeypatch.setattr(
        fresh_interpreter,
        "run_lifecycle_in_fresh_interpreter",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "steps": [
                {"name": "semantic_config", "status": "changed", "message": "enabled"},
                {"name": "semantic_assets", "status": "changed", "message": "rebuilt"},
            ],
        },
    )

    result = _managed_application(root).invoke(RetrievalEnableRequest())

    assert result.status == "ok"
    assert result.result.status is SemanticMaintenanceStatus.CHANGED
    assert result.committed_effects[0].subject == ".brain/local/config.yaml"
    assert result.committed_effects[-1].subject == ".brain/local/embeddings-meta.json"


def test_semantic_enable_failure_after_flag_write_is_partial(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        fresh_interpreter,
        "run_lifecycle_in_fresh_interpreter",
        lambda *_args, **_kwargs: {
            "status": "partial",
            "steps": [
                {"name": "semantic_config", "status": "changed", "message": "enabled"},
                {"name": "semantic_runtime", "status": "error", "message": "provision failed"},
            ],
        },
    )

    result = _managed_application(command_vault_clone.vault_root).invoke(
        RetrievalEnableRequest()
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.committed_effects[0].subject == ".brain/local/config.yaml"


def test_semantic_repair_is_noop_when_vault_has_not_opted_in(command_vault_clone):
    result = _managed_application(command_vault_clone.vault_root).invoke(
        RetrievalRepairSemanticRequest()
    )

    assert result.status == "ok"
    assert result.result.operation == "repair"
    assert result.result.status is SemanticMaintenanceStatus.NOOP
    assert result.committed_effects == ()


def test_semantic_rebuild_forces_full_asset_refresh(command_vault_clone, monkeypatch):
    calls = []

    def fake_run(target, root, **kwargs):
        calls.append((target, root, kwargs))
        return {
            "status": "ok",
            "dry_run": False,
            "steps": [{"name": "semantic_assets", "status": "changed", "message": "rebuilt"}],
            "notes": ["refreshed"],
        }

    monkeypatch.setattr(fresh_interpreter, "run_lifecycle_in_fresh_interpreter", fake_run)

    result = _managed_application(command_vault_clone.vault_root).invoke(
        RetrievalRebuildSemanticRequest()
    )

    assert result.status == "ok"
    assert result.result.operation == "rebuild"
    assert result.result.status is SemanticMaintenanceStatus.CHANGED
    assert calls == [
        (
            "_lifecycle.retrieval_assets:rebuild_semantic_assets",
            command_vault_clone.vault_root,
            {"dry_run": False},
        )
    ]
    assert len(result.committed_effects) == 5


def test_semantic_commands_fail_preflight_without_managed_provider(command_vault_clone):
    result = application_for(
        command_vault_clone.vault_root,
        dependency_tier=DependencyTier.MANAGED,
    ).invoke(RetrievalRepairSemanticRequest())

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert "provider:semantic_runtime" in result.error.details.missing


@pytest.mark.parametrize(
    ("command_id", "request_type", "authority"),
    (
        ("retrieval.enable", RetrievalEnableRequest, Authority.OPERATOR),
        (
            "retrieval.rebuild-semantic",
            RetrievalRebuildSemanticRequest,
            Authority.MAINTAINER,
        ),
        (
            "retrieval.repair-semantic",
            RetrievalRepairSemanticRequest,
            Authority.OPERATOR,
        ),
    ),
)
def test_semantic_transports_are_strict_managed_operator_commands(
    command_id,
    request_type,
    authority,
):
    request = current_request_resolver().resolve(command_id, {})
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.dependency_tier is DependencyTier.MANAGED
    assert entry.required_providers == ("semantic_runtime",)
    assert entry.authority is authority
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(command_id, {"force": True})
