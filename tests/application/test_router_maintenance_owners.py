"""Owner behaviour for compiled-router repair and explicit rebuild."""

from __future__ import annotations

import pytest

from _application._router_maintenance import RouterMaintenanceStatus
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.runtime.refresh_router import RuntimeRefreshRouterRequest
from _application.types import Authority, EffectClass, RetryClass
from _portable import router_maintenance
from command_application import application_for


ROUTER_PATH = ".brain/local/compiled-router.json"


def test_router_repair_is_noop_when_cache_is_fresh(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        RuntimeRefreshRouterRequest()
    )

    assert result.status == "ok"
    assert result.result.status is RouterMaintenanceStatus.NOOP
    assert result.result.forced is False
    assert result.committed_effects == ()


def test_router_repair_rebuilds_stale_cache_and_clears_sidecars(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    (root / ROUTER_PATH).unlink()
    sidecars = (
        ".brain/local/type-embeddings.npy",
        ".brain/local/doc-embeddings.npy",
        ".brain/local/embeddings-meta.json",
    )
    for relative in sidecars:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale")

    result = application_for(root).invoke(RuntimeRefreshRouterRequest())

    assert result.status == "ok"
    assert result.result.status is RouterMaintenanceStatus.CHANGED
    assert result.result.reason == "missing"
    assert result.result.sidecars_removed == sidecars
    assert result.result.session_refreshed is True
    assert result.committed_effects[0].subject == ROUTER_PATH
    assert (root / ROUTER_PATH).is_file()
    assert not any((root / relative).exists() for relative in sidecars)


def test_router_rebuild_is_explicit_even_when_cache_is_fresh(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        RuntimeRefreshRouterRequest(force=True)
    )

    assert result.status == "ok"
    assert result.result.status is RouterMaintenanceStatus.CHANGED
    assert result.result.reason == "explicit-rebuild"
    assert result.result.forced is True
    assert result.committed_effects[0].kind == "runtime.refresh-router"


def test_router_rebuild_dry_run_plans_without_writing(command_vault_clone):
    root = command_vault_clone.vault_root
    before = (root / ROUTER_PATH).read_bytes()

    result = application_for(root, dry_run=True).invoke(
        RuntimeRefreshRouterRequest(force=True)
    )

    assert result.status == "ok"
    assert result.result.status is RouterMaintenanceStatus.PLANNED
    assert result.result.dry_run is True
    assert result.committed_effects == ()
    assert (root / ROUTER_PATH).read_bytes() == before


def test_router_refresh_failure_is_known_partial(command_vault_clone, monkeypatch):
    monkeypatch.setattr(
        router_maintenance.compile_router,
        "refresh_session_markdown",
        lambda *_args: (_ for _ in ()).throw(OSError("session write failed")),
    )

    result = application_for(command_vault_clone.vault_root).invoke(
        RuntimeRefreshRouterRequest(force=True)
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert "session write failed" in result.error.message
    assert result.committed_effects[0].subject == ROUTER_PATH


def test_router_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root

    def fail_after_router_write(_root):
        raise OSError("sidecar cleanup failed after router write")

    monkeypatch.setattr(router_maintenance, "clear_embeddings_outputs", fail_after_router_write)
    result = application_for(root).invoke(RuntimeRefreshRouterRequest(force=True))

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (root / ROUTER_PATH).is_file()


def test_router_maintenance_transport_is_a_strict_maintainer_command():
    command_id = "runtime.refresh-router"
    request = current_request_resolver().resolve(command_id, {"force": True})
    entry = current_application_catalogue().resolve(request)

    assert type(request) is RuntimeRefreshRouterRequest
    assert request.force is True
    assert entry.authority is Authority.MAINTAINER
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(command_id, {"rebuild": True})
