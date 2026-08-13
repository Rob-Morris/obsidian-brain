#!/usr/bin/env python3
"""Capture pinned clients against a localhost model endpoint and real stdio MCP."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

from granular_mcp_metadata import canonical_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "command_interface_real_client_evidence_v1.json"
)
MCP_SERVER = REPO_ROOT / "tests" / "fixtures" / "granular_mcp_stdio_server.py"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
_CAPTURE_REVISION_PLACEHOLDER = "$assembled-document-revision"

SUCCESSFUL_CALLS = (
    ("command.list", {"page_size": 1}),
    (
        "artefact.read",
        {
            "reference": "Designs/project~command-fixture/Command Fixture Design.md",
            "location": "active",
        },
    ),
    ("artefact.list", {"location": "all", "page_size": 1}),
    (
        "document.patch",
        {
            "document": {
                "resource": "artefact",
                "reference": "Designs/project~command-fixture/Command Fixture Design.md",
            },
            "expected_revision": _CAPTURE_REVISION_PLACEHOLDER,
            "old_text": "Second occurrence.",
            "new_text": "Real-client capture.",
            "match": {"mode": "unique"},
        },
    ),
    ("type.sync", {"type_key": "living/journals"}),
    ("runtime.refresh-router", {}),
    ("retrieval.refresh-lexical", {}),
    ("artefact.repair", {"scope": "frontmatter"}),
    ("workspace.read", {"reference": "analysis"}),
    (
        "links.check",
        {"path": "Designs/project~command-fixture/Command Fixture Design.md"},
    ),
    (
        "links.fix",
        {"path": "Designs/project~command-fixture/Command Fixture Design.md"},
    ),
)


def _codex_tool_name(command_id: str) -> str:
    return command_id.replace(".", "_").replace("-", "_")


def _claude_tool_name(command_id: str) -> str:
    return command_id.replace(".", "_")


def _response(output: list[dict], model: str) -> dict:
    return {
        "id": "resp_capture",
        "object": "response",
        "created_at": 1786320000,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": False,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}, "verbosity": "medium"},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 1,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 1,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 2,
        },
        "user": None,
        "metadata": {},
    }


class _CaptureServer:
    def __init__(self, client: str, successful_calls=SUCCESSFUL_CALLS):
        self.client = client
        self.successful_calls = successful_calls
        self.requests: list[dict] = []
        self.review_requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length))
                payload["_capture_path"] = self.path
                if owner.client == "codex-cli" and owner._is_codex_review(payload):
                    owner.review_requests.append(payload)
                    body, content_type = owner._codex_review_response(payload)
                elif owner.client == "codex-cli":
                    owner.requests.append(payload)
                    body, content_type = owner._codex_response(payload)
                else:
                    owner.requests.append(payload)
                    body, content_type = owner._claude_response(payload)
                self.send_response(200)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.httpd.server_port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    def _codex_response(self, payload: dict) -> tuple[bytes, str]:
        number = len(self.requests)
        if number == 1:
            item = {
                "id": "tsc_search",
                "type": "tool_search_call",
                "status": "completed",
                "execution": "client",
                "arguments": {
                    "query": " ".join(command for command, _request in self.successful_calls),
                    "limit": 20,
                },
                "call_id": "call_search",
            }
        elif number <= len(self.successful_calls) + 1:
            command_id, request = self.successful_calls[number - 2]
            item = {
                "id": f"fc_brain_{number - 2}",
                "type": "function_call",
                "status": "completed",
                "arguments": canonical_json(request),
                "call_id": f"call_brain_{number - 2}",
                "name": _codex_tool_name(command_id),
                "namespace": "mcp__brain",
            }
        else:
            item = {
                "id": "msg_final",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "capture complete",
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
        return self._codex_wire(item, payload)

    @staticmethod
    def _is_codex_review(payload: dict) -> bool:
        return (
            payload.get("text", {}).get("format", {}).get("name")
            == "codex_output_schema"
        )

    def _codex_review_response(self, payload: dict) -> tuple[bytes, str]:
        item = {
            "id": f"msg_review_{len(self.review_requests)}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": canonical_json(
                        {
                            "outcome": "allow",
                            "rationale": (
                                "The requested command is confined to the disposable "
                                "capture vault and is explicitly authorised by the test."
                            ),
                            "risk_level": "low",
                            "user_authorization": "high",
                        }
                    ),
                    "annotations": [],
                    "logprobs": [],
                }
            ],
        }
        return self._codex_wire(item, payload)

    @staticmethod
    def _codex_wire(item: dict, payload: dict) -> tuple[bytes, str]:
        response = _response([item], payload.get("model", "capture-model"))
        events = [
            {
                "type": "response.created",
                "response": _response([], response["model"]),
                "sequence_number": 0,
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": item,
                "sequence_number": 1,
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": item,
                "sequence_number": 2,
            },
            {
                "type": "response.completed",
                "response": response,
                "sequence_number": 3,
            },
        ]
        wire = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return wire.encode(), "text/event-stream"

    def _claude_response(self, payload: dict) -> tuple[bytes, str]:
        if payload["_capture_path"].rstrip("/").endswith("count_tokens"):
            return b'{"input_tokens":1}', "application/json"
        number = sum(bool(request.get("tools")) for request in self.requests)
        if payload.get("tools") and number == 1:
            command_id, request = self.successful_calls[0]
            content = [
                {
                    "type": "tool_use",
                    "id": "toolu_capture_0",
                    "name": "mcp__brain__" + _claude_tool_name(command_id),
                    "input": request,
                }
            ]
            stop_reason = "tool_use"
        elif payload.get("tools") and number <= len(self.successful_calls):
            command_id, request = self.successful_calls[number - 1]
            content = [
                {
                    "type": "tool_use",
                    "id": f"toolu_capture_{number - 1}",
                    "name": "mcp__brain__" + _claude_tool_name(command_id),
                    "input": request,
                }
            ]
            stop_reason = "tool_use"
        else:
            content = [{"type": "text", "text": "capture complete"}]
            stop_reason = "end_turn"
        response = {
            "id": "msg_capture",
            "type": "message",
            "role": "assistant",
            "model": payload.get("model", "capture-model"),
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if not payload.get("stream"):
            return json.dumps(response).encode(), "application/json"
        start = {
            **response,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        }
        if stop_reason == "tool_use":
            block = {**content[0], "input": {}}
            delta = {
                "type": "input_json_delta",
                "partial_json": canonical_json(content[0]["input"]),
            }
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": "capture complete"}
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": block},
            ),
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": delta},
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": 1},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        wire = "".join(
            f"event: {event}\ndata: {json.dumps(value)}\n\n"
            for event, value in events
        )
        return wire.encode(), "text/event-stream"


def _mcp_config(vault: Path) -> dict:
    return {
        "mcpServers": {
            "brain": {
                "command": str(PYTHON),
                "args": [str(MCP_SERVER)],
                "env": {"BRAIN_CAPTURE_VAULT": str(vault)},
            }
        }
    }


def _capture_codex(temp: Path, vault: Path) -> dict:
    successful_calls = _resolved_successful_calls(vault)
    codex_home = temp / "codex-home"
    codex_home.mkdir()
    server_config = _mcp_config(vault)["mcpServers"]["brain"]
    model_provider = (
        '{name="capture",base_url="http://127.0.0.1:%d/v1",'
        'env_key="CAPTURE_KEY",wire_api="responses"}'
    )
    with _CaptureServer("codex-cli", successful_calls) as server:
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--approve-for-me",
            "-m",
            "gpt-5.4",
            "-c",
            'model_provider="capture"',
            "-c",
            "model_providers.capture=" + (model_provider % server.port),
            "-c",
            "model_context_window=1000000",
            "-c",
            "model_auto_compact_token_limit=900000",
            "-c",
            "mcp_servers.brain=" + _toml_inline(server_config),
            "Call each requested Brain command in order and report completion.",
        ]
        environment = {**os.environ, "CAPTURE_KEY": "capture", "CODEX_HOME": str(codex_home)}
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return _codex_evidence(server.requests, successful_calls)


def _capture_claude(temp: Path, vault: Path) -> dict:
    successful_calls = _resolved_successful_calls(vault)
    config = temp / "claude-mcp.json"
    config.write_text(json.dumps(_mcp_config(vault)), encoding="utf-8")
    with _CaptureServer("claude-code", successful_calls) as server:
        command = [
            "claude",
            "--bare",
            "--mcp-config",
            str(config),
            "--strict-mcp-config",
            "--allowedTools",
            " ".join(
                "mcp__brain__" + _claude_tool_name(command_id)
                for command_id, _request in successful_calls
            ),
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--model",
            "sonnet",
            "--print",
            "--output-format",
            "json",
            "Call each requested Brain command in order and report completion.",
        ]
        environment = {
            **os.environ,
            "ANTHROPIC_API_KEY": "capture",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.port}",
        }
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return _claude_evidence(server.requests, successful_calls)


def _resolved_successful_calls(vault: Path) -> tuple[tuple[str, dict], ...]:
    """Resolve capture-only dynamic values from one assembled disposable vault."""

    from _common import document_revision_at

    revision = document_revision_at(
        vault / "Designs" / "project~command-fixture" / "Command Fixture Design.md"
    )
    resolved = []
    for command_id, request in SUCCESSFUL_CALLS:
        materialised = json.loads(json.dumps(request))
        if command_id == "document.patch":
            materialised["expected_revision"] = revision
        resolved.append((command_id, materialised))
    return tuple(resolved)


def _toml_inline(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(_toml_inline(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            f"{key}={_toml_inline(item)}" for key, item in value.items()
        ) + "}"
    raise TypeError(f"unsupported TOML capture value: {value!r}")


def _codex_evidence(requests: list[dict], successful_calls) -> dict:
    expected_requests = len(successful_calls) + 2
    if len(requests) != expected_requests:
        summaries = [
            {
                "path": request.get("_capture_path"),
                "input_types": [
                    item.get("type")
                    for item in request.get("input", [])
                    if isinstance(item, dict)
                ],
            }
            for request in requests
        ]
        raise RuntimeError(
            f"Codex capture expected {expected_requests} model requests, got "
            f"{len(requests)}: {canonical_json(summaries)}"
        )
    search_output = next(
        item
        for item in requests[1]["input"]
        if item.get("type") == "tool_search_output"
    )
    namespace = next(item for item in search_output["tools"] if item["name"] == "mcp__brain")
    try:
        declaration = next(
            item for item in namespace["tools"] if item["name"] == "command_list"
        )
    except StopIteration as exc:
        raise RuntimeError(
            "Codex deferred search did not encode dotted command.list: "
            + canonical_json([item.get("name") for item in namespace["tools"]])
        ) from exc
    successes = _codex_successes(requests, successful_calls)
    return {
        "client_version": "0.147.0",
        "capture_path": "deferred client tool search",
        "initial_brain_declarations": 0,
        "declaration": declaration,
        "declaration_hash": _sha256(declaration),
        "minimal_request": {"page_size": 1},
        "minimal_result": {"command": "command.list", "status": "ok"},
        "successful_requests": successes,
    }


def _codex_successes(requests: list[dict], successful_calls) -> dict[str, dict]:
    outputs = {
        item["call_id"]: item["output"]
        for request in requests
        for item in request.get("input", [])
        if item.get("type") == "function_call_output"
    }
    successes = {}
    for index, (command_id, request) in enumerate(successful_calls):
        output = outputs.get(f"call_brain_{index}")
        if not isinstance(output, str):
            raise RuntimeError(f"Codex omitted {command_id} tool output")
        if f'"command":"{command_id}"' not in output or '"status":"ok"' not in output:
            raise RuntimeError(f"Codex {command_id} call was not successful: {output}")
        successes[command_id] = request
    return successes


def _claude_evidence(requests: list[dict], successful_calls) -> dict:
    requests = [
        request
        for request in requests
        if (
            not request["_capture_path"].rstrip("/").endswith("count_tokens")
            and request.get("tools")
        )
    ]
    expected_requests = len(successful_calls) + 1
    if len(requests) != expected_requests:
        raise RuntimeError(
            f"Claude capture expected {expected_requests} message requests, got "
            f"{[(item['_capture_path'], len(item.get('tools', []))) for item in requests]}"
        )
    declarations = [
        tool
        for tool in requests[0].get("tools", [])
        if tool.get("name", "").startswith("mcp__brain__")
    ]
    declaration = next(
        tool for tool in declarations if tool.get("description") == "List command resources."
    )
    successes = _claude_successes(requests, successful_calls)
    return {
        "client_version": "2.1.226",
        "capture_path": "eager model request declarations",
        "initial_brain_declarations": len(declarations),
        "catalogue_hash": _sha256(declarations),
        "declaration": declaration,
        "declaration_hash": _sha256(declaration),
        "minimal_request": {"page_size": 1},
        "minimal_result": {"command": "command.list", "status": "ok"},
        "successful_requests": successes,
    }


def _claude_successes(requests: list[dict], successful_calls) -> dict[str, dict]:
    result_payloads = {}
    for request in requests:
        for message in request.get("messages", []):
            if message.get("role") != "user":
                continue
            for block in message.get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                try:
                    payload = json.loads(block["content"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        "Claude dotted MCP call returned non-JSON tool content: "
                        + canonical_json(block)[-4000:]
                    ) from exc
                if payload.get("status") == "ok":
                    result_payloads[payload.get("command")] = payload
    successes = {}
    for command_id, request in successful_calls:
        if command_id not in result_payloads:
            raise RuntimeError(f"Claude {command_id} call did not return success")
        successes[command_id] = request
    return successes


def _sha256(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def build_capture() -> dict:
    with tempfile.TemporaryDirectory(prefix="brain-real-client-capture-") as directory:
        temp = Path(directory)
        from command_vault import assemble_command_vault_baseline

        def assemble(name: str) -> Path:
            baseline = assemble_command_vault_baseline(
                temp / name,
                machine_state_root=temp / f"{name}-machine-state",
                source_root=REPO_ROOT,
            )
            linked_workspace = temp / f"{name}-analysis-workspace"
            linked_workspace.mkdir()
            workspace_registry = (
                baseline.vault_root / ".brain" / "local" / "workspaces.json"
            )
            workspace_registry.parent.mkdir(parents=True, exist_ok=True)
            workspace_registry.write_text(
                json.dumps(
                    {"workspaces": {"analysis": {"path": str(linked_workspace)}}}
                ),
                encoding="utf-8",
            )
            return baseline.vault_root

        codex_vault = assemble("Brain-codex")
        claude_vault = assemble("Brain-claude")
        return {
            "schema": "brain.command-interface-real-client-evidence/1",
            "captured_at": "2026-08-11T17:00:00+10:00",
            "localhost_model_endpoint": True,
            "external_model_request": False,
            "clients": {
                "claude-code": _capture_claude(temp, claude_vault),
                "codex-cli": _capture_codex(temp, codex_vault),
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(build_capture(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
