"""Trusted local command-context composition tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from _application.adapter import ApplicationAdapter
from _application.receipts import MemoryReceiptStore
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Availability, DependencyTier, SnapshotFreshness
from _command_interface.context import SystemClock, compose_local_context
from _command_interface.profiles import builtin_profile_allow_lists


NOW = datetime.fromisoformat("2026-08-10T06:30:00+10:00")


class _Clock:
    def now(self):
        return NOW


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    (root / ".brain-core").mkdir(parents=True, exist_ok=True)
    (root / ".brain-core" / "VERSION").write_text("0.54.48\n")
    return root


def _context(
    tmp_path,
    *,
    tools=frozenset(("brain_command_list",)),
    invocation_id="inv-local",
):
    clock = _Clock()
    receipts = MemoryReceiptStore(clock)
    return compose_local_context(
        vault_root=_vault(tmp_path),
        brain_id="test-brain",
        profile="reader",
        allowed_tools=tools,
        dependency_tier=DependencyTier.PORTABLE,
        provider_ids=("semantic_retrieval",),
        capability_states=(("semantic_retrieval", Availability.AVAILABLE),),
        snapshot_token="local-snapshot",
        snapshot_freshness=SnapshotFreshness.FRESH,
        snapshot_observed_at=NOW,
        correlation_id="corr-local",
        invocation_id=invocation_id,
        receipt_store=receipts,
        workspace_dir=(tmp_path / "workspace").resolve(),
        clock=clock,
    )


def test_local_context_uses_only_explicit_resolved_state(tmp_path):
    context = _context(tmp_path)

    assert context.selected_brain.brain_id == "test-brain"
    assert context.profile == "reader"
    assert context.dependency_tier is DependencyTier.PORTABLE
    assert context.providers.require("semantic_retrieval").provider_id == "semantic_retrieval"
    assert context.capabilities.availability_of("semantic_retrieval") is Availability.AVAILABLE
    assert context.workspace_dir == (tmp_path / "workspace").resolve()


def test_granular_profile_denies_before_executor_and_has_no_aggregate_fallback(tmp_path):
    adapter = ApplicationAdapter(
        current_application_catalogue(),
        current_request_resolver(),
    )

    allowed = adapter.invoke(
        _context(tmp_path, invocation_id="inv-allowed"),
        "command.list",
        {"page_size": 1},
    )
    denied = adapter.invoke(
        _context(tmp_path, invocation_id="inv-denied"),
        "artefact.list",
        {},
    )

    assert allowed.exit_code == 0
    assert denied.result.error.code is ErrorCode.AUTHORITY_DENIED
    assert denied.exit_code == 3


def test_granular_profile_denial_precedes_dynamic_request_resolution(tmp_path):
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()

    class _FailingResolver:
        entries = resolver.entries

        @staticmethod
        def resolve(*_args, **_kwargs):
            pytest.fail("denied profile must not enter the dynamic resolver")

    result = ApplicationAdapter(catalogue, _FailingResolver()).invoke(
        _context(tmp_path, tools=frozenset(), invocation_id="inv-pre-resolver"),
        "artefact.create",
        {"malformed": "request is deliberately irrelevant"},
    )

    assert result.result.error.code is ErrorCode.AUTHORITY_DENIED
    assert result.exit_code == 3


def test_adapter_bounds_authority_evaluator_failures_before_resolution(tmp_path):
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()
    context = _context(tmp_path, invocation_id="inv-authority-failure")

    class _BrokenAuthority:
        @staticmethod
        def allows(**_kwargs):
            raise RuntimeError("private authority backend detail")

    context = replace(context, authority=_BrokenAuthority())
    result = ApplicationAdapter(catalogue, resolver).invoke(
        context,
        "command.list",
        {"page_size": 1},
    )

    assert result.result.error.code is ErrorCode.INTERNAL_ERROR
    assert "private" not in result.json_text
    assert result.exit_code == 4


def test_built_in_granular_profiles_derive_cumulative_exact_mcp_leaves():
    profiles = builtin_profile_allow_lists(current_application_catalogue())

    assert {name: len(tools) for name, tools in profiles.items()} == {
        "reader": 41,
        "contributor": 82,
        "operator": 109,
    }
    assert set(profiles["reader"]) < set(profiles["contributor"]) < set(
        profiles["operator"]
    )
    assert "brain_command_list" in profiles["reader"]
    assert "brain_artefact_create" in profiles["contributor"]
    assert "brain_artefact_delete" in profiles["operator"]
    assert not set(profiles["operator"]) & {
        "brain_action",
        "brain_create",
        "brain_edit",
        "brain_move",
    }


def test_local_context_refuses_missing_or_symlinked_core_and_open_provider_sets(tmp_path):
    clock = _Clock()
    receipts = MemoryReceiptStore(clock)
    common = dict(
        brain_id="test-brain",
        profile="reader",
        allowed_tools=frozenset(),
        dependency_tier=DependencyTier.PORTABLE,
        capability_states=(),
        snapshot_token="snapshot",
        snapshot_freshness=SnapshotFreshness.FRESH,
        snapshot_observed_at=NOW,
        correlation_id="corr",
        invocation_id="inv",
        receipt_store=receipts,
        clock=clock,
    )
    missing = (tmp_path / "missing").resolve()
    symlinked = (tmp_path / "symlinked").resolve()
    symlinked.mkdir()
    external_core = tmp_path / "external-core"
    external_core.mkdir()
    (external_core / "VERSION").write_text("0.54.48\n")
    (symlinked / ".brain-core").symlink_to(external_core, target_is_directory=True)
    for root, providers in (
        (missing, ()),
        (symlinked, ()),
        (_vault(tmp_path), ("z", "a")),
    ):
        try:
            compose_local_context(vault_root=root, provider_ids=providers, **common)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid local context unexpectedly composed")


def test_system_clock_is_timezone_aware():
    assert SystemClock().now().tzinfo is not None


def test_local_context_requires_already_resolved_absolute_paths(tmp_path):
    common = dict(
        brain_id="test-brain",
        profile="reader",
        allowed_tools=frozenset(),
        dependency_tier=DependencyTier.PORTABLE,
        provider_ids=(),
        capability_states=(),
        snapshot_token="snapshot",
        snapshot_freshness=SnapshotFreshness.FRESH,
        snapshot_observed_at=NOW,
        correlation_id="corr",
        invocation_id="inv",
        receipt_store=MemoryReceiptStore(_Clock()),
        clock=_Clock(),
    )
    with pytest.raises(ValueError, match="vault_root"):
        compose_local_context(vault_root=Path("relative"), **common)
    with pytest.raises(ValueError, match="workspace_dir"):
        compose_local_context(
            vault_root=_vault(tmp_path),
            workspace_dir=Path("relative"),
            **common,
        )
