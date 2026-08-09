"""Deterministic pre-cutover MCP metadata cost capture.

This Phase 1 baseline measures the canonical client wire shapes from raw
FastMCP registration.  It intentionally does not claim to reproduce a client's
private model-facing renderer; Phase 4 adds provenance-bearing real-client
captures and minimal requests before applying release token ceilings.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (
    REPO_ROOT / "src" / "brain-core",
    REPO_ROOT / "src" / "brain-core" / "scripts",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from brain_mcp import server


CAPTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "command_interface_mcp_metadata_baseline_v1.json"
TOKENISER = "brain-unicode-lexeme/1"
BASELINE_BRAIN_CORE_VERSION = "0.54.0"
_LEXEME = re.compile(r"\w+|[^\w\s]", re.UNICODE)

SUPPORTED_CLIENTS = {
    "claude-code": {
        "client_version": "2.1.226",
        "projector": "anthropic-tool-wire-shape/1",
        "version_command": "claude --version",
    },
    "codex-cli": {
        "client_version": "0.147.0",
        "projector": "openai-function-tool-wire-shape/1",
        "version_command": "codex --version",
    },
}


def canonical_json(value) -> str:
    """Encode metadata with deterministic key order and no incidental space."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def metadata_cost(value) -> dict[str, int]:
    """Return raw UTF-8 bytes and pinned relative lexeme units."""
    encoded = canonical_json(value)
    return {
        "raw_bytes": len(encoded.encode("utf-8")),
        "lexeme_tokens": len(_LEXEME.findall(encoded)),
    }


def _project(client: str, tool: dict) -> dict:
    if client == "claude-code":
        return {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["input_schema"],
        }
    if client == "codex-cli":
        return {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["input_schema"],
        }
    raise KeyError(f"unsupported metadata cost projector: {client}")


def _hash(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_metadata_capture() -> dict:
    """Capture raw registration and supported-client relative cost projections."""
    registered = sorted(asyncio.run(server.mcp.list_tools()), key=lambda tool: tool.name)
    tools = [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        }
        for tool in registered
    ]
    source_by_name = {tool["name"]: tool for tool in tools}
    capture = {
        "schema": "brain.command-interface-mcp-metadata-baseline/1",
        "brain_core_version": BASELINE_BRAIN_CORE_VERSION,
        "captured_at": "2026-08-09T09:30:00+10:00",
        "capture_command": ".venv/bin/python tests/capture_mcp_metadata_baseline.py --output tests/fixtures/command_interface_mcp_metadata_baseline_v1.json",
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "tokeniser": TOKENISER,
        "scope": {
            "purpose": "relative pre-cutover metadata cost baseline",
            "real_client_declaration_and_request": "required in Phase 4; not satisfied by this cost capture",
            "release_token_ceiling": "not evaluated with lexeme units",
        },
        "raw_fastmcp": {
            "tool_count": len(tools),
            "catalogue_hash": _hash(tools),
            **metadata_cost(tools),
        },
        "clients": {},
    }
    for client, provenance in SUPPORTED_CLIENTS.items():
        projections = [_project(client, tool) for tool in tools]
        per_tool = {}
        for projected in projections:
            source = source_by_name[projected["name"]]
            per_tool[projected["name"]] = {
                "source_schema_hash": _hash(source["input_schema"]),
                "projected_declaration_hash": _hash(projected),
                **metadata_cost(projected),
            }
        capture["clients"][client] = {
            **provenance,
            "tool_count": len(projections),
            "catalogue_hash": _hash(projections),
            **metadata_cost(projections),
            "tools": per_tool,
        }
    return capture
