#!/usr/bin/env python3
"""Exercise fresh and resumed Grok sessions against disposable Brain and localhost model."""

from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import time
import uuid

from capture_real_mcp_clients import (
    REPO_ROOT,
    PYTHON,
    MCP_SERVER,
    _installed_client_version,
)
from command_vault import assemble_command_vault_baseline
from _bootstrap.readiness import ensure_runtime_warmup, read_runtime_status

OUTPUT = REPO_ROOT / "tests/fixtures/command_interface_grok_client_evidence_v1.json"


def build_capture():
    with tempfile.TemporaryDirectory(prefix="brain-grok-capture-") as directory:
        temp = Path(directory)
        grok_home = temp / "grok-home"
        project = temp / "project"
        grok_home.mkdir()
        project.mkdir()
        config = grok_home / "config.toml"
        config.write_text(
            "[cli]\nuse_leader = false\n[claude_compat]\nimported = true\n"
        )
        vault = assemble_command_vault_baseline(
            temp / "Brain", machine_state_root=temp / "machine", source_root=REPO_ROOT
        ).vault_root
        ensure_runtime_warmup(vault, retry_failed=True)
        deadline = time.monotonic() + 30
        while read_runtime_status(vault)["state"] != "ready":
            if time.monotonic() > deadline:
                raise RuntimeError("Grok capture vault did not warm up")
            time.sleep(0.1)
        requests = []
        turn_requests = []
        phase = "fresh"
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.reply(
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": "grok-4",
                                "object": "model",
                                "created": 1,
                                "owned_by": "xai",
                            }
                        ],
                    }
                )

            def reply(self, value, stream=False):
                body = json.dumps(value)
                if stream:
                    body = "data: " + body + "\n\ndata: [DONE]\n\n"
                self.send_response(200)
                self.send_header(
                    "content-type",
                    "text/event-stream" if stream else "application/json",
                )
                self.end_headers()
                self.wfile.write(body.encode())

            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers.get("content-length", "0")))
                )
                requests.append((self.path, payload))
                declarations = payload.get("tools", [])
                discovery_available = any(
                    t.get("function", {}).get("name") == "search_tool"
                    for t in declarations
                )
                message = {"role": "assistant", "content": "capture complete"}
                finish = "stop"
                if discovery_available:
                    turn_requests.append(payload)
                    index = len(turn_requests) - 1
                    if index == 0:
                        name, arguments = "search_tool", {
                            "query": "brain",
                            "limit": 100,
                        }
                    elif index <= len(calls):
                        command, request = calls[index - 1]
                        name, arguments = "use_tool", {
                            "tool_name": "brain__" + command.replace(".", "_"),
                            "tool_input": request,
                        }
                    else:
                        name = None
                    if name is not None:
                        message = {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"{phase}_{index}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        }
                        finish = "tool_calls"
                response = {
                    "id": "chatcmpl-capture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "grok-4",
                    "choices": [
                        {"index": 0, "message": message, "finish_reason": finish}
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
                if payload.get("stream"):
                    response["object"] = "chat.completion.chunk"
                    response["choices"][0]["delta"] = response["choices"][0].pop(
                        "message"
                    )
                    for index, call in enumerate(
                        response["choices"][0]["delta"].get("tool_calls", [])
                    ):
                        call["index"] = index
                self.reply(response, payload.get("stream", False))

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        environment = {
            **os.environ,
            "GROK_HOME": str(grok_home),
            "GROK_CONFIG_PATH": str(config),
            "GROK_XAI_API_BASE_URL": url + "/v1",
            "XAI_API_BASE_URL": url + "/v1",
            "GROK_CLI_CHAT_PROXY_BASE_URL": url,
            "GROK_MODELS_BASE_URL": url,
            "GROK_MODELS_LIST_URL": url + "/models",
            "XAI_API_KEY": "capture",
            "GROK_CODE_XAI_API_KEY": "capture",
            "GROK_TELEMETRY_EVENTS_URL": url,
            "GROK_TELEMETRY_BUILD_EVENTS_URL": url,
            "GROK_MANAGED_CONFIG_URL": url + "/config",
            "GROK_TELEMETRY_TRACE_UPLOAD": "false",
        }
        version = _installed_client_version("grok")
        session_id = str(uuid.uuid4())
        evidence = {
            "schema": "brain.command-interface-grok-evidence/1",
            "client_version": version,
            "captured_at": datetime.now().astimezone().isoformat(),
            "localhost_model_endpoint": True,
            "external_model_request": False,
            "sessions": {},
        }
        try:
            subprocess.run(
                [
                    "grok",
                    "mcp",
                    "add",
                    "--scope",
                    "user",
                    "brain",
                    "-e",
                    f"BRAIN_CAPTURE_VAULT={vault}",
                    "--",
                    str(PYTHON),
                    str(MCP_SERVER),
                ],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            for phase in ("fresh", "resumed"):
                calls = [
                    ("session.start", {}),
                    ("artefact.read", {"reference": "Projects/Command Fixture.md"}),
                    (
                        "artefact.create",
                        {
                            "type": "thought",
                            "title": f"Grok {phase} Capture",
                            "frontmatter": {"tags": ["capture"]},
                        },
                    ),
                ]
                turn_requests.clear()
                command = [
                    "grok",
                    "--leader-socket",
                    str(temp / "leader.sock"),
                    "--cwd",
                    str(project),
                    "--debug-file",
                    str(temp / "debug.log"),
                    "--model",
                    "grok-4",
                    "--no-subagents",
                    "--disable-web-search",
                    "--always-approve",
                    "--max-turns",
                    "8",
                    "--output-format",
                    "json",
                    "--session-id" if phase == "fresh" else "--resume",
                    session_id,
                    "-p",
                    "Call the requested Brain tools in order.",
                ]
                completed = subprocess.run(
                    command,
                    cwd=project,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if completed.returncode:
                    raise RuntimeError(
                        f"Grok failed: {completed.stderr[-2000:]} {completed.stdout[-2000:]}"
                    )
                if not turn_requests:
                    raise RuntimeError(
                        f"Grok exposed no Brain tools; paths: {[p for p, _ in requests]}; output: {completed.stdout[-2000:]}"
                    )
                outputs = {
                    m.get("tool_call_id"): m.get("content")
                    for r in turn_requests
                    for m in r.get("messages", [])
                    if m.get("role") == "tool"
                }
                for index, (command_id, _) in enumerate(calls):
                    output = outputs.get(f"{phase}_{index + 1}", "")
                    if command_id + ": ok" not in str(output) and not (
                        '"status"' in str(output)
                        and '"ok"' in str(output)
                        and command_id in str(output)
                    ):
                        raise RuntimeError(
                            f"Grok {phase} {command_id} unsuccessful: {output}"
                        )
                discovery = json.loads(outputs[f"{phase}_0"])
                observed = [
                    tool
                    for result in discovery["results"]
                    if result["server"] == "brain"
                    for tool in result["tools"]
                ]
                declarations = sorted(
                    (
                        {
                            key: tool[key]
                            for key in ("tool_name", "description", "input_schema")
                        }
                        for tool in observed
                    ),
                    key=lambda tool: tool["tool_name"],
                )
                wanted = {
                    "brain__session_start",
                    "brain__artefact_read",
                    "brain__artefact_create",
                    "brain__document_update-frontmatter",
                }
                evidence["sessions"][phase] = {
                    "tool_names": [tool["tool_name"] for tool in declarations],
                    "catalogue_hash": "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            declarations, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    "declarations": [
                        tool for tool in declarations if tool["tool_name"] in wanted
                    ],
                    "successful_requests": dict(calls),
                    "same_session": True,
                }
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
        return evidence


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build_capture(), indent=2, sort_keys=True) + "\n")
