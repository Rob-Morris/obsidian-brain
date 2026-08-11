"""Pinned real-client declarations and minimal MCP request evidence."""

from __future__ import annotations

import hashlib
import json

from granular_mcp_metadata import (
    REPO_ROOT,
    SUPPORTED_CLIENTS,
    _registered_tools,
    canonical_json,
    project_tool,
)
from capture_real_mcp_clients import SUCCESSFUL_CALLS


EVIDENCE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "command_interface_real_client_evidence_v1.json"
)


def _hash(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def test_pinned_real_clients_observe_current_command_list_declaration():
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    command_list = next(
        tool for tool in _registered_tools() if tool["name"] == "command.list"
    )

    assert evidence["schema"] == "brain.command-interface-real-client-evidence/1"
    assert evidence["localhost_model_endpoint"] is True
    assert evidence["external_model_request"] is False
    assert set(evidence["clients"]) == set(SUPPORTED_CLIENTS)
    for client, expected in SUPPORTED_CLIENTS.items():
        observed = evidence["clients"][client]
        declaration = project_tool(client, command_list)
        assert observed["client_version"] == expected["client_version"]
        assert observed["declaration"] == declaration
        assert observed["declaration_hash"] == _hash(declaration)
        assert observed["minimal_request"] == {"page_size": 1}
        assert observed["minimal_result"] == {
            "command": "command.list",
            "status": "ok",
        }
        assert observed["successful_requests"] == dict(SUCCESSFUL_CALLS)


def test_real_clients_cover_eager_and_deferred_projection_paths():
    clients = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))["clients"]
    registered = _registered_tools()
    claude_catalogue = [project_tool("claude-code", tool) for tool in registered]

    assert clients["claude-code"]["capture_path"] == "eager model request declarations"
    assert clients["claude-code"]["initial_brain_declarations"] == len(registered) == 78
    assert clients["claude-code"]["catalogue_hash"] == _hash(claude_catalogue)
    assert clients["codex-cli"]["capture_path"] == "deferred client tool search"
    assert clients["codex-cli"]["initial_brain_declarations"] == 0
