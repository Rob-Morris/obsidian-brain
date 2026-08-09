"""Pre-cutover supported-client MCP metadata cost evidence."""

from __future__ import annotations

import json

from mcp_metadata_cost import (
    CAPTURE_PATH,
    SUPPORTED_CLIENTS,
    TOKENISER,
    build_metadata_capture,
)


def _capture() -> dict:
    return json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))


def test_pre_cutover_metadata_capture_matches_current_fastmcp_registration():
    assert _capture() == build_metadata_capture()


def test_capture_pins_supported_client_projectors_and_measurement_method():
    capture = _capture()

    assert capture["schema"] == "brain.command-interface-mcp-metadata-baseline/1"
    assert capture["tokeniser"] == TOKENISER
    assert set(capture["clients"]) == set(SUPPORTED_CLIENTS)
    assert capture["raw_fastmcp"]["tool_count"] == 22
    for client, expected in SUPPORTED_CLIENTS.items():
        projected = capture["clients"][client]
        assert projected["client_version"] == expected["client_version"]
        assert projected["projector"] == expected["projector"]
        assert projected["version_command"] == expected["version_command"]
        assert projected["tool_count"] == 22
        assert len(projected["tools"]) == 22
        assert projected["raw_bytes"] > 0
        assert projected["lexeme_tokens"] > 0


def test_cost_capture_does_not_masquerade_as_the_phase_four_real_client_gate():
    scope = _capture()["scope"]

    assert scope["purpose"] == "relative pre-cutover metadata cost baseline"
    assert scope["real_client_declaration_and_request"].startswith("required in Phase 4")
    assert scope["release_token_ceiling"] == "not evaluated with lexeme units"
