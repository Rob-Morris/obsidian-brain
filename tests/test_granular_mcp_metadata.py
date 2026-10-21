"""Hard tool bounds, known catalogue target excess and measured regression evidence."""

from __future__ import annotations

import json
from pathlib import Path

from granular_mcp_metadata import (
    CAPTURE_PATH,
    COMPACT_TOOL_TOKENS,
    COMPACT_TOOL_EXCEPTIONS,
    FULL_CATALOGUE_TARGET_TOKENS,
    CATALOGUE_REGRESSION_BASELINE,
    MAX_TOOL_TOKENS,
    SUPPORTED_CLIENTS,
    TOKENISER,
    _registered_tools,
    build_granular_metadata_capture,
)


def _capture() -> dict:
    return json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))


def test_granular_projection_capture_matches_raw_fastmcp_registration():
    assert _capture() == build_granular_metadata_capture()


def test_supported_client_projections_obey_hard_and_compact_tool_bounds():
    capture = _capture()

    assert capture["schema"] == "brain.command-interface-granular-mcp-projection/1"
    assert capture["tokeniser"] == TOKENISER
    assert capture["ceilings"] == {
        "per_tool_tokens": MAX_TOOL_TOKENS,
    }
    assert capture["compact_tools"] == {"target_tokens": 512, "exceptions": COMPACT_TOOL_EXCEPTIONS}
    assert all(reason.strip() for reason in COMPACT_TOOL_EXCEPTIONS.values())
    observed_large = set()
    assert set(capture["clients"]) == set(SUPPORTED_CLIENTS)
    assert capture["raw_fastmcp"]["tool_count"] == 90
    for client, expected in SUPPORTED_CLIENTS.items():
        projected = capture["clients"][client]
        assert {
            field: projected[field]
            for field in (
                "client_version",
                "version_command",
                "projector",
                "projector_source",
            )
        } == expected
        assert projected["tool_count"] == 90
        assert projected["maximum_tool_tokens"] <= MAX_TOOL_TOKENS
        large = {name for name, metadata in projected["tools"].items()
                 if metadata["tokens"] > COMPACT_TOOL_TOKENS}
        assert large <= set(COMPACT_TOOL_EXCEPTIONS)
        observed_large.update(large)
        assert all(
            item["tokens"] <= MAX_TOOL_TOKENS
            for item in projected["tools"].values()
        )

    assert observed_large == set(COMPACT_TOOL_EXCEPTIONS)


def test_catalogue_reports_known_target_excess_and_preserves_measured_no_growth_ratchet():
    capture = _capture()
    assert FULL_CATALOGUE_TARGET_TOKENS == 20_000
    assert CATALOGUE_REGRESSION_BASELINE == {"claude-code": 21_465, "codex-cli": 21_501}
    assert capture["catalogue_budget"]["target_tokens"] == 20_000
    assert capture["catalogue_budget"]["regression_baseline_tokens"] == CATALOGUE_REGRESSION_BASELINE
    assert "not measured host loading" in capture["catalogue_budget"]["scope"]
    assert "review follows" in capture["catalogue_budget"]["review"]
    for client, row in capture["clients"].items():
        assert row["tokens"] <= CATALOGUE_REGRESSION_BASELINE[client]
        assert row["budget"] == {
            "target_tokens": 20_000,
            "target_exceeded": row["tokens"] > 20_000,
            "over_target_tokens": max(0, row["tokens"] - 20_000),
            "regression_baseline_tokens": CATALOGUE_REGRESSION_BASELINE[client],
            "regression_exceeded": False,
        }


def test_projection_evidence_retains_source_and_declaration_hashes_per_tool():
    capture = _capture()

    for projected in capture["clients"].values():
        assert len(projected["tools"]) == projected["tool_count"]
        for evidence in projected["tools"].values():
            assert evidence["source_schema_hash"].startswith("sha256:")
            assert evidence["projected_declaration_hash"].startswith("sha256:")


def test_granular_projection_has_no_unexplained_per_tool_cost_regression():
    current = _capture()["clients"]
    baseline_path = (
        Path(__file__).parent
        / "fixtures"
        / "command_interface_mcp_metadata_baseline_v1.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["clients"]

    for client in SUPPORTED_CLIENTS:
        current_average = current[client]["raw_bytes"] / current[client]["tool_count"]
        baseline_average = baseline[client]["raw_bytes"] / baseline[client]["tool_count"]
        assert current_average <= baseline_average * 2


def test_every_projected_description_is_bounded_to_forty_words():
    pending = _registered_tools()
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            description = node.get("description")
            if isinstance(description, str):
                assert len(description.split()) <= 40
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
