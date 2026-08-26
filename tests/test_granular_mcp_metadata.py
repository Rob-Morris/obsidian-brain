"""Release gates for the staged granular MCP projection."""

from __future__ import annotations

import json
from pathlib import Path

from granular_mcp_metadata import (
    CAPTURE_PATH,
    COMPACT_TOOL_TOKENS,
    LARGE_TOOL_ALLOWLIST,
    MAX_CATALOGUE_TOKENS,
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


def test_supported_client_projections_obey_release_token_ceilings():
    capture = _capture()

    assert capture["schema"] == "brain.command-interface-granular-mcp-projection/1"
    assert capture["tokeniser"] == TOKENISER
    assert capture["ceilings"] == {
        "per_tool_tokens": MAX_TOOL_TOKENS,
        "full_catalogue_tokens": MAX_CATALOGUE_TOKENS,
    }
    assert set(capture["clients"]) == set(SUPPORTED_CLIENTS)
    assert capture["raw_fastmcp"]["tool_count"] == 68
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
        assert projected["tool_count"] == 68
        assert projected["tokens"] <= MAX_CATALOGUE_TOKENS
        assert projected["maximum_tool_tokens"] <= MAX_TOOL_TOKENS
        assert {
            name
            for name, metadata in projected["tools"].items()
            if metadata["tokens"] > COMPACT_TOOL_TOKENS
        } <= LARGE_TOOL_ALLOWLIST
        assert all(
            item["tokens"] <= MAX_TOOL_TOKENS
            for item in projected["tools"].values()
        )


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
