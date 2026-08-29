"""Phase 4 metadata evidence for the staged granular MCP projection."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import tiktoken
from mcp.server import MCPServer


REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (
    REPO_ROOT / "src" / "brain-core",
    REPO_ROOT / "src" / "brain-core" / "scripts",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from brain_mcp._command_adapter import register_application_tools
from _application.registry import current_application_catalogue, current_request_resolver


CAPTURE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "command_interface_granular_mcp_projection_v1.json"
)
REAL_CLIENT_CAPTURE_PATH = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "command_interface_real_client_evidence_v1.json"
)
TOKENISER = "tiktoken/0.12.0:o200k_base"
TOKEN_ENCODING = "o200k_base"
COMPACT_TOOL_TOKENS = 512
MAX_TOOL_TOKENS = 3_072
LARGE_TOOL_ALLOWLIST = frozenset({"document.structured-edit", "resource.create"})
MAX_CATALOGUE_TOKENS = 16_384


def _real_client_capture() -> dict[str, object]:
    return json.loads(REAL_CLIENT_CAPTURE_PATH.read_text(encoding="utf-8"))


def _supported_clients() -> dict[str, dict[str, str]]:
    evidence = _real_client_capture()
    clients = evidence["clients"]
    return {
        "claude-code": {
            "client_version": clients["claude-code"]["client_version"],
            "version_command": "claude --version",
            "projector": (
                "claude-code-model-tool-declaration/"
                + clients["claude-code"]["client_version"]
            ),
            "projector_source": "captured model request plus deterministic replay",
        },
        "codex-cli": {
            "client_version": clients["codex-cli"]["client_version"],
            "version_command": "codex --version",
            "projector": (
                "codex-cli-responses-function-declaration/"
                + clients["codex-cli"]["client_version"]
            ),
            "projector_source": "captured model request plus deterministic replay",
        },
    }


SUPPORTED_CLIENTS = _supported_clients()


def canonical_json(value) -> str:
    """Encode metadata with deterministic ordering and no incidental space."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _registered_tools() -> list[dict[str, object]]:
    mcp = MCPServer("brain-granular-projection-capture")
    register_application_tools(
        mcp,
        catalogue=current_application_catalogue(),
        resolver=current_request_resolver(),
        context_factory=lambda **_metadata: None,
        invocation_guard=lambda: None,
    )
    registered = sorted(asyncio.run(mcp.list_tools()), key=lambda tool: tool.name)
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in registered
    ]


def project_tool(client: str, tool: dict[str, object]) -> dict[str, object]:
    """Replay the declaration shape observed from one pinned real client."""

    if client == "claude-code":
        return {
            "name": f"mcp__brain__{tool['name'].replace('.', '_')}",
            "description": tool["description"],
            "input_schema": tool["input_schema"],
        }
    if client == "codex-cli":
        return {
            "type": "function",
            "name": tool["name"].replace(".", "_").replace("-", "_"),
            "description": tool["description"],
            "strict": False,
            "defer_loading": True,
            "parameters": _codex_parameters(tool["input_schema"]),
        }
    raise KeyError(f"unsupported granular MCP client: {client}")


def _codex_parameters(schema: dict[str, object]) -> dict[str, object]:
    """Replay Codex's observed MCP-to-Responses schema normalisation."""

    def visit(value):
        if isinstance(value, dict):
            return {
                key: visit(item)
                for key, item in value.items()
                if key != "default"
            }
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    projected = visit(schema)
    return projected


def _cost(value, encoding) -> dict[str, int]:
    wire = canonical_json(value)
    return {
        "raw_bytes": len(wire.encode("utf-8")),
        "tokens": len(encoding.encode(wire)),
    }


def build_granular_metadata_capture() -> dict[str, object]:
    """Build reproducible raw-registration and supported-client cost evidence."""

    tools = _registered_tools()
    encoding = tiktoken.get_encoding(TOKEN_ENCODING)
    capture: dict[str, object] = {
        "schema": "brain.command-interface-granular-mcp-projection/1",
        "captured_at": _real_client_capture()["captured_at"],
        "capture_command": (
            ".venv/bin/python tests/capture_granular_mcp_projection.py "
            "--output tests/fixtures/command_interface_granular_mcp_projection_v1.json"
        ),
        "mcp_sdk_version": importlib.metadata.version("mcp"),
        "tokeniser": TOKENISER,
        "ceilings": {
            "per_tool_tokens": MAX_TOOL_TOKENS,
            "full_catalogue_tokens": MAX_CATALOGUE_TOKENS,
        },
        "raw_fastmcp": {
            "tool_count": len(tools),
            "catalogue_hash": _hash(tools),
            **_cost(tools, encoding),
        },
        "clients": {},
    }
    for client, provenance in SUPPORTED_CLIENTS.items():
        projections = [project_tool(client, tool) for tool in tools]
        per_tool = {}
        for source, projected in zip(tools, projections, strict=True):
            name = source["name"]
            per_tool[name] = {
                "source_schema_hash": _hash(source["input_schema"]),
                "projected_declaration_hash": _hash(projected),
                **_cost(projected, encoding),
            }
        capture["clients"][client] = {
            **provenance,
            "tool_count": len(projections),
            "catalogue_hash": _hash(projections),
            **_cost(projections, encoding),
            "maximum_tool_tokens": max(item["tokens"] for item in per_tool.values()),
            "tools": per_tool,
        }
    return capture
