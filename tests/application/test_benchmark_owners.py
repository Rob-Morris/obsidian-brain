"""Owner behaviour for retrieval benchmark construction and evaluation."""

from __future__ import annotations

import json

import pytest

from _application.registry import current_application_catalogue, current_request_resolver
from _application.retrieval.construct_benchmark import (
    BenchmarkConstructionStatus,
    RetrievalConstructBenchmarkRequest,
)
from _application.retrieval.evaluate import RetrievalEvaluateRequest
from _application.types import (
    Authority,
    DependencyTier,
    EffectClass,
    Projection,
    RetryClass,
)
from command_application import application_for
import construct_benchmark_fixture
import evaluate_search


def _managed_application(root, *, dry_run=False):
    return application_for(
        root,
        dependency_tier=DependencyTier.MANAGED,
        dry_run=dry_run,
    )


def test_construct_benchmark_dry_run_resolves_outputs_without_writes(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        construct_benchmark_fixture,
        "construct_fixture",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not construct"),
    )
    root = command_vault_clone.vault_root

    result = _managed_application(root, dry_run=True).invoke(
        RetrievalConstructBenchmarkRequest("_Temporal/benchmarks/demo.json")
    )

    assert result.status == "ok"
    assert result.result.status is BenchmarkConstructionStatus.PLANNED
    assert result.result.fixture_path == "_Temporal/benchmarks/demo.json"
    assert result.result.audit_path == "_Temporal/benchmarks/demo.audit.json"
    assert result.committed_effects == ()
    assert not (root / result.result.fixture_path).exists()


def test_construct_benchmark_reports_bounded_output_effects(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(construct_benchmark_fixture, "_load_runtime_modules", lambda: None)
    monkeypatch.setattr(
        construct_benchmark_fixture,
        "construct_fixture",
        lambda *_args, **_kwargs: {
            "fixture_case_count": 7,
            "semantic_available": False,
            "semantic_error": "disabled",
            "summary": {"lexical-expected": {"admitted": 7}},
        },
    )

    result = _managed_application(command_vault_clone.vault_root).invoke(
        RetrievalConstructBenchmarkRequest(
            "_Temporal/benchmarks/demo.json",
            "_Temporal/benchmarks/demo-audit.json",
        )
    )

    assert result.status == "ok"
    assert result.result.fixture_case_count == 7
    assert result.result.semantic_available is False
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "_Temporal/benchmarks/demo.json",
        "_Temporal/benchmarks/demo-audit.json",
    )


def test_construct_benchmark_rejects_paths_outside_selected_brain(
    command_vault_clone,
):
    result = _managed_application(command_vault_clone.vault_root).invoke(
        RetrievalConstructBenchmarkRequest("../outside.json")
    )

    assert result.status == "error"
    assert result.effects == "none"


def test_construct_benchmark_rejects_protected_system_output(command_vault_clone):
    result = _managed_application(command_vault_clone.vault_root).invoke(
        RetrievalConstructBenchmarkRequest(".brain/local/benchmark.json")
    )

    assert result.status == "error"
    assert result.effects == "none"


def test_evaluate_returns_report_without_effects(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    benchmark = root / ".brain/local/benchmark.json"
    benchmark.parent.mkdir(parents=True, exist_ok=True)
    benchmark.write_text(json.dumps({"cases": []}), encoding="utf-8")
    monkeypatch.setattr(evaluate_search, "_load_runtime_modules", lambda: None)
    monkeypatch.setattr(
        evaluate_search,
        "build_report",
        lambda *_args, **_kwargs: {
            "benchmark": {
                "path": str(benchmark),
                "description": "fixture",
                "case_count": 2,
                "hit_ks": [1, 3, 5],
            },
            "modes": [
                {"mode": "lexical", "status": "ok"},
                {"mode": "hybrid", "status": "unavailable"},
            ],
            "comparisons": [],
            "expected_winner_scorecard": {},
        },
    )

    result = _managed_application(root).invoke(
        RetrievalEvaluateRequest(
            ".brain/local/benchmark.json",
            ("lexical", "hybrid"),
        )
    )

    assert result.status == "ok"
    assert result.result.case_count == 2
    assert result.result.hit_ks == (1, 3, 5)
    assert result.result.modes == ("lexical", "hybrid")
    assert result.committed_effects == ()


@pytest.mark.parametrize(
    ("command_id", "request_type", "payload", "effect", "retry"),
    (
        (
            "retrieval.construct-benchmark",
            RetrievalConstructBenchmarkRequest,
            {"fixture_path": "_Temporal/benchmark.json"},
            EffectClass.SELECTED_BRAIN_MUTATION,
            RetryClass.RECEIPT_REQUIRED,
        ),
        (
            "retrieval.evaluate",
            RetrievalEvaluateRequest,
            {"benchmark_path": "_Temporal/benchmark.json"},
            EffectClass.NONE,
            RetryClass.SAFE,
        ),
    ),
)
def test_benchmark_transports_are_non_mcp_managed_maintainer_commands(
    command_id,
    request_type,
    payload,
    effect,
    retry,
):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.dependency_tier is DependencyTier.MANAGED
    assert entry.authority is Authority.MAINTAINER
    assert entry.effect_class is effect
    assert entry.retry_class is retry
    assert entry.eligible_projections == (
        Projection.CLI,
        Projection.SCRIPT,
        Projection.PYTHON,
    )
    assert entry.projections[0].projection is Projection.MCP
    assert entry.projections[0].supported is False
    assert entry.projections[0].reason
