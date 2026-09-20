"""Blocked startup and standing streams use the production transport lifecycle."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from brain_mcp._proxy_handoff import public_session
from brain_mcp._proxy_session import SUBSCRIPTION_ID
from test_mcp_proxy import _read_until_id, _read_json_messages
from mcp_server.test_production_proxy_protocols import MODERN_META, SERVER


def test_subscription_first_receives_child_events_without_another_request(command_vault_clone):
    vault = command_vault_clone.vault_root
    process = subprocess.Popen([sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        frame = {"jsonrpc": "2.0", "id": "first", "method": "subscriptions/listen", "params": {
            "_meta": MODERN_META, "notifications": {"resourceSubscriptions": ["brain://resource"]}}}
        process.stdin.write((json.dumps(frame) + "\n").encode())
        process.stdin.flush()
        # The second event can only follow the real child's internal ACK. Send
        # no discovery, ping or tool call that could accidentally initialise it.
        messages = _read_json_messages(process, timeout=20, max_count=2)
        assert [event["method"] for event in messages] == [
            "notifications/subscriptions/acknowledged", "notifications/resources/updated"]
        assert all(event["params"]["_meta"][SUBSCRIPTION_ID] == "first" for event in messages)
        assert messages[1]["params"]["uri"] == "brain://resource"
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("replacement", ["none", "image", "runtime"])
def test_blocked_startup_activates_same_session_and_preserves_streams(command_vault_clone, modern, replacement):
    if replacement != "none" and sys.platform == "win32":
        pytest.skip("POSIX image replacement")
    vault = command_vault_clone.vault_root
    process = subprocess.Popen([sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment,
             "BRAIN_CAPTURE_VAULT": str(vault), "BRAIN_CAPTURE_BLOCKED": "1"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sequence = 0
    notifications = []

    def send(method, params, request_id):
        if modern:
            params = {**params, "_meta": MODERN_META}
        frame = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            frame["id"] = request_id
        process.stdin.write((json.dumps(frame) + "\n").encode())
        process.stdin.flush()

    def request(method, params):
        nonlocal sequence
        sequence += 1
        send(method, params, sequence)
        messages = _read_until_id(process, sequence, timeout=25)
        notifications.extend(message for message in messages if "id" not in message)
        matches = [message for message in messages if message.get("id") == sequence]
        assert len(matches) == 1, (messages, process.poll())
        return matches[0]

    def call(name):
        return request("tools/call", {"name": name, "arguments": {}})["result"]

    try:
        initial = request("server/discover" if modern else "initialize", {} if modern else {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "lifecycle", "version": "1"}})
        assert "experimental" not in initial["result"]["capabilities"]
        if not modern:
            send("notifications/initialized", {}, None)
        assert len(request("tools/list", {})["result"]["tools"]) == 3
        assert call("brain_proxy_status")["structuredContent"]["result"]["lifecycle"]["phase"] == "blocked"
        if modern:
            send("subscriptions/listen", {"notifications": {"toolsListChanged": True}}, "tools-stream")
            send("subscriptions/listen", {"notifications": {"promptsListChanged": True}}, "prompts-stream")
            request("ping", {})
            acks = [event for event in notifications if event["method"] == "notifications/subscriptions/acknowledged"]
            assert {event["params"]["_meta"][SUBSCRIPTION_ID] for event in acks} == {"tools-stream", "prompts-stream"}
        refused = call("brain_proxy_restart")
        assert refused["isError"] is True
        assert refused["structuredContent"]["error"]["effects"] == "none"
        (vault / "repaired").touch()
        if replacement == "image":
            image = vault / ".brain-core/brain_mcp/proxy.py"
            image.write_text(image.read_text() + "\n# test installed image replacement\n")
        elif replacement == "runtime":
            requirements = vault / ".brain-core/brain_mcp/requirements.txt"
            requirements.write_text(requirements.read_text() + "\n# repaired dependency environment\n")
            subprocess.run([sys.executable, str(SERVER.parent / "managed_proxy_runtime.py"), str(vault)],
                           check=True, capture_output=True)
            assert call("brain_proxy_refresh")["structuredContent"]["error"]["code"] == "runtime_restart_required"
        recovered = call("brain_proxy_restart")
        assert recovered["isError"] is False, recovered
        assert recovered["structuredContent"]["result"]["lifecycle"]["phase"] == "ready"
        runtime = recovered["structuredContent"]["result"]["runtime"]
        assert runtime["loaded"] == runtime["child"] == runtime["required"]
        assert len(request("tools/list", {})["result"]["tools"]) > 3
        final = request("server/discover", {}) if modern else initial
        assert public_session(initial) == public_session(final)
        read = request("tools/call", {"name": "artefact_read", "arguments": {"reference": "Projects/Command Fixture.md"}})["result"]
        assert read["isError"] is False, read
        assert read["structuredContent"]["result"]["content"] == (vault / "Projects/Command Fixture.md").read_text()
        if modern:
            events = [event for event in notifications if event["method"] == "notifications/tools/list_changed"]
            assert events and all(event["params"]["_meta"][SUBSCRIPTION_ID] == "tools-stream" for event in events)
            assert call("brain_proxy_refresh")["isError"] is False
            send("notifications/cancelled", {"requestId": "tools-stream"}, None)
            request("ping", {})
            notifications.clear()
            version = vault / ".brain-core/VERSION"
            version.write_text(version.read_text().strip() + ".stream-refresh\n")
            assert call("brain_proxy_refresh")["isError"] is False
            assert not any(event["method"] == "notifications/tools/list_changed" for event in notifications)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.returncode:
            print(process.stderr.read().decode(errors="replace")[-5000:])
