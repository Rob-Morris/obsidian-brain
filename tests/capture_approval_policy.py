"""Opt-in native client approval probes using localhost model and harmless tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import shlex

from capture_real_mcp_clients import _CaptureServer, _toml_inline


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "tests/fixtures/approval_policy_server.py"
PYTHON = ROOT / ".venv/bin/python"


class ShellModel(_CaptureServer):
    def _claude_response(self, payload):
        body, content_type = super()._claude_response(payload)
        return body.replace(b"mcp__brain__shell_execute", b"Bash"), content_type


class CodexShellModel(_CaptureServer):
    def _codex_response(self, payload):
        if len(self.requests) == 1:
            item = {"id": "fc_fixture", "type": "function_call", "status": "completed", "call_id": "fixture_shell",
                    "name": "exec_command", "arguments": json.dumps(self.successful_calls[0][1])}
        else:
            item = {"id": "msg_fixture", "type": "message", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": "Fixture complete.", "annotations": []}]}
        return self._codex_wire(item, payload)


def codex_shell_probe(verb):
    sys.path.insert(0, str(ROOT / "src/brain-core/scripts"))
    from _bootstrap.approval_clients import Selection, desired_items, render_items
    with tempfile.TemporaryDirectory(prefix="brain-approval-codex-shell-") as directory:
        root = Path(directory).resolve()
        executable = root / "brain"
        executable.write_text("#!/bin/sh\nprintf 'APPROVAL_SHELL_CALLED\\n'\n")
        executable.chmod(0o700)
        config = root / "codex"
        (config / "rules").mkdir(parents=True)
        (config / "config.toml").write_text("")
        selection = Selection("codex", "user", "cli", config, executable)
        policy = {json.dumps(("artefact", "read")): "allow", json.dumps(("artefact", "delete")): "prompt"}
        selection.path.write_text(render_items(selection, "", desired_items(selection, policy)))
        call = {"cmd": shlex.join((str(executable), "artefact", verb))}
        with CodexShellModel("codex-cli", (("shell.execute", call),)) as model:
            process = subprocess.run(["codex", "exec", "--skip-git-repo-check", "--approve-for-me", "--disable", "plugins",
                                      "-m", "gpt-5.4", "-c", 'model_provider="capture"', "-c",
                                      'model_providers.capture={name="capture",base_url="http://127.0.0.1:%d/v1",env_key="CAPTURE_KEY",wire_api="responses"}' % model.port,
                                      "Run the harmless temporary shell fixture requested by the model."],
                                     cwd=root, env={**os.environ, "CODEX_HOME": str(config), "CAPTURE_KEY": "local-fixture"},
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            return {"client": "codex-cli", "shell": verb, "called": "APPROVAL_SHELL_CALLED" in json.dumps(model.requests),
                    "reviews": len(model.review_requests), "exit_code": process.returncode,
                    "stderr": process.stderr[-1200:] if process.returncode else None}


def shell_probe(command_suffix: str, restricted: bool = False, *, spaces=False) -> dict:
    with tempfile.TemporaryDirectory(prefix="brain-approval-shell-") as directory:
        temp = Path(directory)
        executable = temp / ("brain with spaces" if spaces else "brain")
        calls = temp / "calls"
        executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + shlex.quote(str(calls)) + "\nprintf 'APPROVAL_SHELL_CALLED\\n'\n", encoding="utf-8")
        executable.chmod(0o700)
        prefix = shlex.quote(str(executable))
        command = f"{prefix} {command_suffix.replace('{brain}', prefix)}"
        with ShellModel("claude-code", (("shell.execute", {"command": command, "description": "Harmless shell approval fixture"}),)) as model:
            config = temp / "claude"
            config.mkdir()
            permissions = {"allow": [f"Bash({prefix} artefact read *)"]}
            if restricted:
                permissions["ask"] = [f"Bash({prefix} artefact read *)"]
            (config / "settings.json").write_text(json.dumps({"permissions": permissions}), encoding="utf-8")
            env = {**os.environ, "CLAUDE_CONFIG_DIR": str(config), "ANTHROPIC_API_KEY": "local-fixture",
                   "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{model.port}"}
            result = subprocess.run(["claude", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                                     "--permission-mode", "dontAsk", "--model", "sonnet", "--print",
                                     "--output-format", "json", "Run the harmless shell fixture."],
                                    cwd=temp, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=45)
            # Inspect tool results only: the command itself contains no output marker.
            return {"client": "claude-code", "shell": command_suffix, "restricted": restricted,
                    "called": "APPROVAL_SHELL_CALLED" in json.dumps(model.requests),
                    "executed": calls.read_text().splitlines() if calls.exists() else [],
                    "spaces": spaces,
                    "exit_code": result.returncode,
                    "error": result.stderr[-1500:] if result.returncode else None}


def probe(client: str, decision: str, *, scope="user", trusted=False) -> dict:
    with tempfile.TemporaryDirectory(prefix="brain-approval-proof-") as directory:
        temp = Path(directory)
        calls = (("document.structured-edit", {}),)
        if scope != "user":
            subprocess.run(["git", "init", "--quiet", str(temp)], check=True, capture_output=True)
        with _CaptureServer(client, calls) as model:
            transport = {"command": str(PYTHON), "args": [str(SERVER)]}
            environment = dict(os.environ)
            if client == "codex-cli":
                config = temp / "codex"
                config.mkdir()
                environment.update(CODEX_HOME=str(config), CAPTURE_KEY="local-fixture")
                server = {**transport, "default_tools_approval_mode": "prompt",
                          "tools": {"document_structured-edit": {"approval_mode": decision}}}
                native_root = config if scope == "user" else temp / ".codex"
                native_root.mkdir(exist_ok=True)
                (native_root / "config.toml").write_text("[mcp_servers.brain]\n" + "\n".join(f"{k}={_toml_inline(v)}" for k, v in server.items()) + "\n")
                if scope == "project":
                    subprocess.run(["git", "init", "--quiet", str(temp)], check=True, capture_output=True)
                    (config / "config.toml").write_text(f"[projects.{json.dumps(str(temp))}]\ntrust_level=\"trusted\"\n")
                command = ["codex", "exec", "--skip-git-repo-check", "--approve-for-me", "--disable", "plugins",
                           "-m", "gpt-5.4", "-c", 'model_provider="capture"', "-c",
                           'model_providers.capture={name="capture",base_url="http://127.0.0.1:%d/v1",env_key="CAPTURE_KEY",wire_api="responses"}' % model.port,
                           "Run the single harmless fixture tool requested by the local model."]
            else:
                config = temp / "claude"
                config.mkdir()
                environment.update(CLAUDE_CONFIG_DIR=str(config), ANTHROPIC_API_KEY="local-fixture",
                                   ANTHROPIC_BASE_URL=f"http://127.0.0.1:{model.port}")
                settings = {"permissions": {decision: ["mcp__brain__document_structured-edit"]}}
                native_root = config if scope == "user" else temp / ".claude"
                native_root.mkdir(exist_ok=True)
                (native_root / ("settings.local.json" if scope == "local" else "settings.json")).write_text(json.dumps(settings), encoding="utf-8")
                if trusted:
                    (config / ".claude.json").write_text(json.dumps({"projects": {str(temp.resolve()): {"hasTrustDialogAccepted": True}}}))
                mcp = temp / "mcp.json"
                mcp.write_text(json.dumps({"mcpServers": {"brain": transport}}), encoding="utf-8")
                command = ["claude", "--strict-mcp-config", "--mcp-config", str(mcp),
                           "--setting-sources", "user,project,local",
                           "--debug-file", str(temp / "debug.log"),
                           "--permission-mode", "dontAsk", "--model", "sonnet", "--print",
                           "--output-format", "json", "Run the single harmless fixture tool requested by the local model."]
            result = subprocess.run(command, cwd=temp, env=environment, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=45)
            requests = json.dumps(model.requests)
            return {"client": client, "scope": scope, "trusted": trusted, "decision": decision, "exit_code": result.returncode,
                    "called": "APPROVAL_FIXTURE_CALLED:" in requests,
                    "reviews": len(model.review_requests),
                    "error": result.stderr[-1500:] if result.returncode else None,
                    "request_count": len(model.requests),
                    "tool_errors": [block for request in model.requests for message in request.get("messages", [])
                                    for block in message.get("content", []) if isinstance(block, dict)
                                    and block.get("type") == "tool_result" and block.get("is_error")],
                    "settings_debug": [line for line in (temp / "debug.log").read_text().splitlines() if "setting" in line.lower()]
                    if (temp / "debug.log").exists() and "APPROVAL_FIXTURE_CALLED:" not in requests and decision == "allow" else []}


def codex_rules_probe():
    sys.path.insert(0, str(ROOT / "src/brain-core/scripts"))
    from _bootstrap.approval_clients import Selection, desired_items, render_items
    with tempfile.TemporaryDirectory(prefix="brain-approval-rules-") as directory:
        root = Path(directory)
        executable = root / "brain with spaces"
        selection = Selection("codex", "user", "cli", root, executable)
        desired = desired_items(selection, {json.dumps(("artefact", "read")): "allow", json.dumps(("artefact", "delete")): "prompt"})
        rules = root / "brain.rules"
        rules.write_text(render_items(selection, "", desired))
        for words, expected in (((str(executable), "artefact", "read"), "allow"),
                                ((str(executable), "artefact", "delete"), "prompt"),
                                ((str(executable), "--vault", "/tmp/example", "artefact", "read"), None),
                                (("sh", "-c", str(executable) + " artefact read"), None)):
            process = subprocess.run(["codex", "execpolicy", "check", "--rules", str(rules), "--", *words],
                                     env={**os.environ, "CODEX_HOME": str(root / "codex")}, capture_output=True, text=True, timeout=30)
            result = json.loads(process.stdout)
            assert process.returncode == 0 and result.get("decision") == expected, result
            print(json.dumps({"client": "codex-cli", "words": list(words[1:]), "expected": expected, "result": result}), flush=True)


if __name__ == "__main__":
    if "--codex-shell" in sys.argv:
        for verb in ("read", "delete"):
            result = codex_shell_probe(verb)
            print(json.dumps(result), flush=True)
            assert result["called"] and result["exit_code"] == 0, result
            assert result["reviews"] == (0 if verb == "read" else 1), result
        raise SystemExit(0)
    if "--rules" in sys.argv:
        codex_rules_probe()
        raise SystemExit(0)
    if "--shell" in sys.argv:
        for suffix, restricted in (("artefact read", False), ("artefact read --request-json '{}'", False),
                                   ("artefact delete", False), ("artefact read", True),
                                   ("artefact read && {brain} artefact delete", False),
                                   ("artefact read $({brain} artefact delete)", False)):
            result = shell_probe(suffix, restricted)
            assert not any("artefact delete" in call for call in result["executed"]), result
            print(json.dumps(result), flush=True)
        result = shell_probe("artefact read", spaces=True)
        assert result["called"], result
        print(json.dumps(result), flush=True)
    else:
        for client, decisions in (("codex-cli", ("approve", "prompt")), ("claude-code", ("allow", "ask"))):
            if "--claude" in sys.argv and client != "claude-code":
                continue
            for scope in (("user", "project") if client == "codex-cli" else ("user", "project", "local")):
                for decision in decisions:
                    result = probe(client, decision, scope=scope, trusted=scope == "project" and client == "claude-code")
                    assert result["exit_code"] == 0, result
                    assert result["called"] == (decision != "ask"), result
                    assert result["reviews"] == (1 if decision == "prompt" else 0), result
                    print(json.dumps(result), flush=True)
