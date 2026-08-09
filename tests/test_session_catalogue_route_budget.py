"""Budgets for the probe-free static session catalogue route."""

from __future__ import annotations

from pathlib import Path

from session_catalogue_route_budget import (
    COLD_PROCESS_COUNT,
    WARM_SAMPLE_COUNT,
    WARMUP_COUNT,
    cold_samples,
    nearest_rank_p95,
    warm_samples,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_static_session_route_meets_size_import_and_latency_budgets():
    vault_root = REPO_ROOT / "template-vault"
    cold = cold_samples(vault_root)
    warm = warm_samples(vault_root)
    cold_p95 = nearest_rank_p95([int(item["elapsed_ns"]) for item in cold])
    warm_p95 = nearest_rank_p95(warm)
    diagnostics = {
        "runner": "ubuntu-latest/Python-3.12/release-lock",
        "cold_processes": COLD_PROCESS_COUNT,
        "warmups": WARMUP_COUNT,
        "warm_samples": WARM_SAMPLE_COUNT,
        "cold_p95_ns": cold_p95,
        "warm_p95_ns": warm_p95,
        "cold_samples": cold,
    }
    violations = []
    if any(item["application_imported"] for item in cold):
        violations.append("application import")
    if any(int(item["route_bytes"]) > 512 for item in cold):
        violations.append("route size")
    if cold_p95 > 20_000_000:
        violations.append("cold p95")
    if warm_p95 > 5_000_000:
        violations.append("warm p95")
    if violations:
        rerun_cold = cold_samples(vault_root)
        rerun_warm = warm_samples(vault_root)
        diagnostics["diagnostic_rerun"] = {
            "reason": "retain one identically prepared rerun for noisy failure",
            "cold_p95_ns": nearest_rank_p95(
                [int(item["elapsed_ns"]) for item in rerun_cold]
            ),
            "warm_p95_ns": nearest_rank_p95(rerun_warm),
            "cold_samples": rerun_cold,
        }

    assert not violations, diagnostics
