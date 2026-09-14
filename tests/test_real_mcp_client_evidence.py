"""Pinned real-client declarations and minimal MCP request evidence."""

from __future__ import annotations

import hashlib
import json

from granular_mcp_metadata import (
    REPO_ROOT,
    SUPPORTED_CLIENTS,
    canonical_json,
    project_tool,
)
from capture_real_mcp_clients import (
    SUCCESSFUL_CALLS,
    _CAPTURE_REVISION_PLACEHOLDER,
    RESUMED_CALLS,
)

EVIDENCE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "command_interface_real_client_evidence_v1.json"
)


def _hash(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def test_pinned_real_clients_retain_the_recorded_command_list_declaration():
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    historical = json.loads(EVIDENCE_PATH.with_name("command_interface_granular_mcp_projection_v1.json").read_text())

    assert evidence["schema"] == "brain.command-interface-real-client-evidence/1"
    assert evidence["localhost_model_endpoint"] is True
    assert evidence["external_model_request"] is False
    assert set(evidence["clients"]) == set(SUPPORTED_CLIENTS)
    for client, expected in SUPPORTED_CLIENTS.items():
        observed = evidence["clients"][client]
        declaration = observed["declaration"]
        assert observed["client_version"] == expected["client_version"]
        assert observed["declaration_hash"] == _hash(declaration)
        assert _hash(declaration) == historical["clients"][client]["tools"]["command_list"]["projected_declaration_hash"]
        assert observed["minimal_request"] == {"page_size": 1}
        assert observed["minimal_result"] == {
            "command": "command.list",
            "status": "ok",
        }
        successful_requests = json.loads(
            json.dumps(observed["successful_requests"])
        )
        revision = successful_requests["document.replace-text"]["expected_revision"]
        assert revision.startswith("sha256:") and len(revision) == 71
        successful_requests["document.replace-text"][
            "expected_revision"
        ] = _CAPTURE_REVISION_PLACEHOLDER
        assert successful_requests == dict(SUCCESSFUL_CALLS)
        assert observed["resumed"]["same_session"]
        assert observed["resumed"]["successful_requests"] == dict(RESUMED_CALLS)


def test_pinned_real_clients_cover_eager_and_deferred_projection_paths():
    clients = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))["clients"]
    historical = json.loads(EVIDENCE_PATH.with_name("command_interface_granular_mcp_projection_v1.json").read_text())
    captured_count = historical["raw_fastmcp"]["tool_count"]

    assert clients["claude-code"]["capture_path"] == "eager model request declarations"
    assert clients["claude-code"]["initial_brain_declarations"] == captured_count
    assert clients["claude-code"]["catalogue_hash"].startswith("sha256:")
    assert clients["codex-cli"]["capture_path"] in {
        "deferred client tool search",
        "eager and deferred client tools",
    }
    assert (
        0
        <= clients["codex-cli"]["initial_brain_declarations"]
        < captured_count
    )


def test_grok_fresh_and_resumed_sessions_discover_portable_names_and_invoke():
    import re

    path = EVIDENCE_PATH.with_name("command_interface_grok_client_evidence_v1.json")
    evidence = json.loads(path.read_text())
    assert evidence["client_version"] == "1.0.30"
    assert (
        evidence["localhost_model_endpoint"] and not evidence["external_model_request"]
    )
    historical = json.loads(EVIDENCE_PATH.with_name("command_interface_granular_mcp_projection_v1.json").read_text())
    expected = historical["clients"]["claude-code"]["tools"]
    for phase in ("fresh", "resumed"):
        observed = evidence["sessions"][phase]
        assert observed["same_session"]
        assert observed["tool_names"] == sorted("brain__" + name for name in expected)
        assert all(
            re.fullmatch(r"[a-zA-Z0-9_-]+", name) for name in observed["tool_names"]
        )
        for declaration in observed["declarations"]:
            name = declaration["tool_name"].removeprefix("brain__")
            source = {"name": name, "description": declaration["description"],
                      "input_schema": declaration["input_schema"]}
            assert _hash(source["input_schema"]) == expected[name]["source_schema_hash"]
            assert _hash(project_tool("claude-code", source)) == expected[name]["projected_declaration_hash"]
        assert set(observed["successful_requests"]) == {
            "session.start",
            "artefact.read",
            "artefact.create",
        }


def test_grok_capture_uses_brain_native_setup():
    path = EVIDENCE_PATH.with_name("command_interface_grok_client_evidence_v1.json")
    setup = json.loads(path.read_text())["native_setup"]
    assert setup == {
        "owner": "Brain",
        "config": ".grok/config.toml",
        "rule_discovered": True,
        "shaping_discovered": True,
        "inherited_registration_required": False,
    }
