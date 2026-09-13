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
from _bootstrap import agent_skills, grok_mcp
from _bootstrap.file_transaction import FilePlan, apply_file_changes

OUTPUT = REPO_ROOT / "tests/fixtures/command_interface_grok_client_evidence_v1.json"


def _model_visible_envelope(output) -> dict:
    """Parse the structured envelope Grok forwarded to the model as tool text."""

    if isinstance(output, dict) and output.get("status"):
        return output
    text = output if isinstance(output, str) else json.dumps(output)
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    for candidate in text.replace("\r", "\n").split("\n"):
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError(f"Grok model-visible tool text was not JSON: {output!r}")


def build_capture():
    with tempfile.TemporaryDirectory(prefix="brain-grok-capture-") as directory:
        temp = Path(directory).resolve()
        client_home = temp / "home"
        grok_home = client_home / ".grok"
        project = temp / "project"
        grok_home.mkdir(parents=True)
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
            plan = FilePlan()
            grok_mcp.plan_configure(
                plan,
                client_home,
                {
                    "command": str(PYTHON),
                    "args": [str(MCP_SERVER)],
                    "env": {"BRAIN_CAPTURE_VAULT": str(vault)},
                },
            )
            apply_file_changes(plan.changes())
            steps = agent_skills.configure_agent_skill_adapters(
                home_dir=client_home, client="grok"
            )
            if steps[0]["status"] != "changed":
                raise RuntimeError("Brain did not install the Grok skill adapter")
            inspected = subprocess.run(
                ["grok", "inspect", "--json"],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            discovery = json.loads(inspected.stdout)
            native_mcp = next(
                item for item in discovery["mcpServers"] if item["name"] == "brain"
            )
            rule = grok_home / "rules" / "brain.md"
            skill = grok_home / "skills" / "shaping" / "SKILL.md"
            if not any(
                Path(item["path"]) == rule for item in discovery["projectInstructions"]
            ):
                raise RuntimeError("Grok did not discover Brain's native startup rule")
            if not any(
                Path(item["source"].get("path", "")) == skill
                for item in discovery["skills"]
            ):
                raise RuntimeError(
                    "Grok did not discover Brain's native shaping adapter"
                )
            if Path(native_mcp["source"]["path"]) != config:
                raise RuntimeError(
                    "Grok selected an inherited registration instead of Brain's native one"
                )
            evidence["native_setup"] = {
                "owner": "Brain",
                "config": ".grok/config.toml",
                "rule_discovered": True,
                "shaping_discovered": True,
                "inherited_registration_required": False,
            }
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
                    envelope = _model_visible_envelope(output)
                    if (
                        envelope.get("schema") != "brain.command-result/1"
                        or envelope.get("command") != command_id
                        or envelope.get("status") != "ok"
                        or not isinstance(envelope.get("result"), dict)
                    ):
                        raise RuntimeError(
                            f"Grok {phase} {command_id} model-visible text "
                            f"was not the structured envelope: {output!r}"
                        )
                    result = envelope["result"]
                    if command_id == "session.start":
                        if (
                            result.get("brain_core_version") != (vault / ".brain-core/VERSION").read_text().strip()
                            or not result.get("core_bootstrap")
                            or not result.get("command_catalogue")
                        ):
                            raise RuntimeError("Grok did not receive the material bootstrap")
                    elif command_id == "artefact.read":
                        if result.get("content") != (vault / "Projects/Command Fixture.md").read_text():
                            raise RuntimeError("Grok did not receive the exact artefact body")
                    elif command_id == "artefact.create":
                        if not (vault / result["path"]).is_file() or not any(
                            effect["subject"] == result["path"]
                            for effect in envelope.get("committed_effects", [])
                        ):
                            raise RuntimeError("Grok did not receive the committed creation result")
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
