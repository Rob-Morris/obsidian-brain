"""Material payload and invocation ownership through the production relay."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from proxy_test_support import _read_until_id, _finish_proxy_with_diagnostics


SERVER = Path(__file__).resolve().parents[1] / "fixtures/production_mcp_proxy.py"
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "protocol-test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def refresh_router_after_core_upgrade(vault):
    """Model upgrade.py's post-commit compile through real router maintenance."""
    from _common import vault_mutation_lock
    from _portable.router_maintenance import maintain_router

    with vault_mutation_lock(vault):
        result = maintain_router(vault, dry_run=False, force=False)
    assert result.status == "changed", result
    assert result.reason == "version-drift", result
    router = json.loads((vault / ".brain/local/compiled-router.json").read_text())
    assert router["meta"]["brain_core_version"] == (vault / ".brain-core/VERSION").read_text().strip()


@pytest.mark.parametrize("modern", [False, True])
def test_real_owned_outcome_is_recovered_after_response_loss(command_vault_clone, modern):
    vault = command_vault_clone.vault_root
    marker = vault / "lost-mutation-response"
    adapter = vault / ".brain-core/brain_mcp/_command_adapter.py"
    original = adapter.read_text()
    signature = "def _tool_result(projection) -> CallToolResult:\n"
    assert original.count(signature) == 1
    adapter.write_text(original.replace(signature, signature +
        "    from pathlib import Path\n    import os\n"
        f"    marker = Path({str(marker)!r})\n"
        "    if projection.result.command_id == 'artefact.create' and not marker.exists():\n"
        "        marker.touch()\n        os._exit(42)\n"))
    with (vault.parent / "outcome-recovery-stderr.log").open("w+b") as stderr:
        frames = []
        phase = "launch"
        process = subprocess.Popen([sys.executable, str(SERVER)], cwd=vault,
            env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True)

        def request(i, method, params):
            nonlocal phase
            if modern:
                params = {**params, "_meta": MODERN_META}
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            phase = f"reply to {method} id={i} (25s deadline)"
            messages = _read_until_id(process, i, timeout=25)
            frames.extend(messages)
            reply = next((m for m in messages if m.get("id") == i), None)
            assert reply is not None, f"No reply to id={i} within 25s"
            return reply

        try:
            request(1, "server/discover" if modern else "initialize", {} if modern else {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "owned-outcome", "version": "1"}})
            if not modern:
                process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                process.stdin.flush()
            result = request(2, "tools/call", {"name": "artefact_create", "arguments": {
                "type": "thought", "title": "Recovered response"}})["result"]
            envelope = result["structuredContent"]
            assert marker.exists()
            assert envelope["schema"] == "brain.proxy-outcome-resolution/1", envelope
            assert envelope["outcome"]["execution"] == "succeeded"
            assert envelope["outcome"]["receipt"]["state"] == "committed"
            assert result["isError"] is False
            assert len(list(vault.rglob("*Recovered response.md"))) == 1
        finally:
            _finish_proxy_with_diagnostics(process, frames, stderr, phase=phase)


@pytest.mark.parametrize("modern", [False, True])
def test_frozen_protocol_four_negotiates_refresh_then_receives_restart_gate(command_vault_clone, modern):
    vault = command_vault_clone.vault_root
    invoked = vault.parent / "application-handler-invoked"
    owner = vault / ".brain-core/scripts/_application/artefact/read.py"
    owner.write_text(owner.read_text().replace(
        "def execute(context, request):\n",
        f"def execute(context, request):\n    from pathlib import Path\n    Path({str(invoked)!r}).touch()\n"))
    process = subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault),
             "BRAIN_CAPTURE_PROTOCOL_FOUR": "1"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0
    def request(method, params):
        nonlocal sequence
        sequence += 1
        if modern:
            params = {**params, "_meta": MODERN_META}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        return next(reply for reply in _read_until_id(process, sequence, timeout=20)
                    if reply.get("id") == sequence)["result"]
    def call(name, arguments=None):
        return request("tools/call", {"name": name, "arguments": arguments or {}})
    try:
        initial = request("server/discover" if modern else "initialize", {} if modern else {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "v4-test", "version": "1"}})
        if not modern:
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        assert initial["capabilities"]["experimental"]["brainCommandInterface"]["proxy_protocol"] == {"minimum": 4, "maximum": 5}
        before = call("brain_proxy_status")["structuredContent"]["result"]
        version = vault / ".brain-core/VERSION"
        version.write_text(version.read_text().strip() + ".gate-upgrade\n")
        refresh_router_after_core_upgrade(vault)
        refused = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        envelope = refused["structuredContent"]
        assert refused["isError"] is True
        assert envelope["schema"] == "brain.proxy-gate-result/1"
        assert envelope["error"]["code"] == "proxy_restart_required"
        assert envelope["error"]["effects"] == "none"
        assert envelope["error"]["details"]["running_proxy_protocol"] == 4
        assert envelope["error"]["details"]["required_proxy_protocol"] == {"minimum": 5, "maximum": 5}
        assert not invoked.exists()
        after = call("brain_proxy_status")["structuredContent"]["result"]
        assert after["interface"]["generation"] == before["interface"]["generation"] + 1
        assert after["server"]["loaded"] == version.read_text().strip()
        assert after["server"]["available"] is True
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("protocol", ["2025-06-18", "2026-07-28", "modern-without-discover"])
def test_production_proxy_delivers_reads_and_mutations(command_vault_clone, protocol):
    vault = command_vault_clone.vault_root
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0

    def request(method, params):
        nonlocal sequence
        sequence += 1
        if protocol != "2025-06-18":
            params = {**params, "_meta": {**MODERN_META, **params.get("_meta", {})}}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        replies = _read_until_id(process, sequence, timeout=20)
        return next(reply for reply in replies if reply.get("id") == sequence)

    def call(name, arguments, **extra):
        return request("tools/call", {"name": name, "arguments": arguments, **extra})["result"]

    try:
        if protocol == "2025-06-18":
            response = request("initialize", {"protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
            assert response.get("result", {}).get("protocolVersion") == protocol, response
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        elif protocol == "2026-07-28":
            response = request("server/discover", {})
            assert "brainCommandInterface" in response["result"]["capabilities"]["experimental"]

        if protocol == "modern-without-discover":
            first = call("brain_proxy_status", {})
            assert first["isError"] is False
            first_refresh = call("brain_proxy_refresh", {})
            assert first_refresh["isError"] is False, first_refresh
        discovery = request("tools/list", {})["result"]["tools"]
        names = [tool["name"] for tool in discovery]
        assert names.count("brain_proxy_status") == names.count("brain_proxy_refresh") == 1
        before = call("brain_proxy_status", {})["structuredContent"]["result"]
        assert before["server"]["refresh"] == "current"
        # Includes the compact, separate tool-discovery observation (~65 bytes).
        assert len(json.dumps(before).encode()) < 1300

        read = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        envelope = read["structuredContent"]
        assert read["isError"] is False
        assert envelope["result"]["content"] == (vault / "Projects/Command Fixture.md").read_text()
        assert json.loads(read["content"][0]["text"]) == envelope

        injected = request("tools/call", {"name": "artefact_read", "arguments": {"reference": "Projects/Command Fixture.md"},
                            "_meta": {"brainInvocation": {"invocationId": "mcp-caller-owned"}}})
        assert injected["error"]["code"] == -32602
        assert "owned by the proxy" in injected["error"]["message"]
        assert "rediscover" not in json.dumps(injected)

        # Normal content creation starts authorised; no lease request is needed.
        created = call("artefact_create", {"type": "thought", "title": "Protocol round trip"})
        assert created["isError"] is False
        result = created["structuredContent"]
        assert (vault / result["result"]["path"]).exists()
        assert result["committed_effects"][0]["subject"] == result["result"]["path"]
        assert json.loads(created["content"][0]["text"]) == result
        prepared = call("access_prepare", {"preparation": {"kind": "operation", "command_id": "artefact.read",
                        "arguments": {"reference": "Projects/Command Fixture.md"}}})
        assert prepared["isError"] is False
        operation = prepared["structuredContent"]["result"]
        grant = call("access_request", {"consent": {"scope": "operation",
                     **{key: operation[key] for key in ("operation_id", "digest", "review")}}})
        assert grant["isError"] is False
        # Idle drift is detected before this semantic call is accepted. The
        # existing proxy validates and swaps its child, then dispatches once.
        marker = vault / ".brain-core/VERSION"
        marker.write_text(marker.read_text().strip() + ".review\n")
        refresh_router_after_core_upgrade(vault)
        selected = call("artefact_read", {"reference": "Projects/Command Fixture.md", "brain_operation": operation["operation_id"]})
        assert selected["isError"] is False, selected["structuredContent"].get("error")
        assert selected["structuredContent"]["result"] == envelope["result"]
        after = call("brain_proxy_status", {})["structuredContent"]["result"]
        assert after["server"]["loaded"] == marker.read_text().strip()
        assert after["interface"]["generation"] == before["interface"]["generation"] + 1
        spent = call("artefact_read", {"reference": "Projects/Command Fixture.md", "brain_operation": operation["operation_id"]})
        assert spent["isError"] is True
        marker.write_text(marker.read_text().strip() + ".explicit\n")
        refreshed = call("brain_proxy_refresh", {})
        assert refreshed["isError"] is False
        assert refreshed["structuredContent"]["result"]["server"]["loaded"] == marker.read_text().strip()
        assert json.loads(refreshed["content"][0]["text"]) == refreshed["structuredContent"]
        router_path = vault / ".brain/local/compiled-router.json"
        stale_router = router_path.read_bytes()
        stale = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert stale["isError"] is True
        refused = stale["structuredContent"]
        assert refused["error"]["code"] == "conflict", refused
        assert refused["result"] is None
        assert refused["error"]["effects"] == "none"
        assert "producer version is outdated" in refused["error"]["message"]
        assert "runtime.refresh-router" in refused["error"]["message"]
        assert router_path.read_bytes() == stale_router, "Reader must not repair derived state"
        repaired = call("runtime_refresh-router", {})
        assert repaired["isError"] is False, repaired
        repaired_envelope = repaired["structuredContent"]
        assert repaired_envelope["result"]["status"] == "changed"
        assert repaired_envelope["result"]["reason"] == "version-drift"
        assert any(effect["subject"] == ".brain/local/compiled-router.json"
                   for effect in repaired_envelope["committed_effects"])
        assert json.loads(router_path.read_text())["meta"]["brain_core_version"] == marker.read_text().strip()
        fresh = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert fresh["isError"] is False, fresh
        assert fresh["structuredContent"]["result"] == envelope["result"]

    finally:
        process.terminate()
        process.wait(timeout=5)


def test_incompatible_candidate_keeps_connection_and_can_be_repaired(command_vault_clone):
    vault = command_vault_clone.vault_root
    process = subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0

    def request(method, params):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method,
                                      "params": {**params, "_meta": MODERN_META}}) + "\n")
        process.stdin.flush()
        return next(reply for reply in _read_until_id(process, sequence, timeout=20) if reply.get("id") == sequence)

    def call(name):
        return request("tools/call", {"name": name, "arguments": {}})["result"]

    try:
        request("server/discover", {})
        before = call("brain_proxy_status")["structuredContent"]["result"]
        contract = vault / ".brain-core/brain_mcp/_interface_protocol.py"
        original = contract.read_text()
        contract.write_text(original.replace("MIN_PROXY_PROTOCOL = 4", "MIN_PROXY_PROTOCOL = 99")
                           .replace("MAX_PROXY_PROTOCOL = 5", "MAX_PROXY_PROTOCOL = 99"))
        version = vault / ".brain-core/VERSION"
        version.write_text(version.read_text().strip() + ".incompatible\n")
        refresh_router_after_core_upgrade(vault)
        blocked = call("brain_proxy_refresh")
        assert blocked["isError"] is True
        state = blocked["structuredContent"]["result"]
        assert state["server"]["refresh"] == "blocked"
        assert state["server"]["loaded"] == before["server"]["loaded"]
        assert state["server"]["available"] is True
        assert state["interface"]["generation"] == before["interface"]["generation"]
        # The old server is still alive and its connection has not been killed.
        discovery = request("server/discover", {})["result"]
        assert discovery["capabilities"]["experimental"]["brainCommandInterface"]["proxy_protocol"]["minimum"] == 4
        contract.write_text(original)
        recovered = call("brain_proxy_refresh")
        assert recovered["isError"] is False, recovered
        assert recovered["structuredContent"]["result"]["server"]["refresh"] == "current"
        owner = vault / ".brain-core/scripts/_application/artefact/read.py"
        changed_version = int(re.search(r'COMMAND_VERSION: ClassVar\[int\] = (\d+)', owner.read_text())[1]) + 1
        changed_owner, count = re.subn(r'(COMMAND_VERSION: ClassVar\[int\] = )(\d+)',
            lambda match: match[1] + str(changed_version), owner.read_text())
        assert count == 1, 'fixture must change the actual command contract'
        owner.write_text(changed_owner)
        version.write_text(version.read_text().strip() + ".changed-contract\n")
        refresh_router_after_core_upgrade(vault)
        read_params = {"name": "artefact_read", "arguments": {"reference": "Projects/Command Fixture.md"}}
        changed = request("tools/call", read_params)["result"]
        assert changed["isError"] is True
        assert changed["structuredContent"]["error"]["code"] == "interface_changed"
        assert changed["structuredContent"]["error"]["effects"] == "none"
        request("tools/list", {})
        rediscovered = request("tools/call", read_params)["result"]
        assert rediscovered["isError"] is False
        assert rediscovered["structuredContent"]["command_version"] == changed_version
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("modern", [False, True])
def test_changed_discovery_commands_recover_through_host_listing(command_vault_clone, modern):
    vault = command_vault_clone.vault_root
    process = subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0

    def request(method, params):
        nonlocal sequence
        sequence += 1
        if modern:
            params = {**params, "_meta": MODERN_META}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        return next(reply for reply in _read_until_id(process, sequence, timeout=20)
                    if reply.get("id") == sequence)["result"]

    def call(name, arguments=None):
        return request("tools/call", {"name": name, "arguments": arguments or {}})

    def status():
        return call("brain_proxy_status")["structuredContent"]["result"]

    try:
        request("server/discover" if modern else "initialize", {} if modern else {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "rediscovery-test", "version": "1"}})
        if not modern:
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        request("tools/list", {})
        assert status()["interface"]["tool_discovery"]["state"] == "current"
        contracts = vault / ".brain-core/scripts/_application/requests.py"
        original = contracts.read_text()
        updated = original
        for command in ("command.list", "command.describe"):
            old = f'COMMAND_ID: ClassVar[str] = "{command}"\n    COMMAND_VERSION: ClassVar[int] = 5'
            assert old in updated
            updated = updated.replace(old, old[:-1] + "6")
        contracts.write_text(updated + "\n")
        version = vault / ".brain-core/VERSION"
        version.write_text(version.read_text().strip() + ".discovery\n")
        refresh_router_after_core_upgrade(vault)
        refreshed = call("brain_proxy_refresh")["structuredContent"]["result"]
        assert refreshed["server"]["refresh"] == "current"
        assert refreshed["next_action"] == "rediscover_tools"
        pending = refreshed["interface"]["tool_discovery"]
        assert pending["pending_tools"] == ["command_describe", "command_list"]
        assert pending["recovery"]["method"] == "tools/list"
        assert pending["recovery"]["owner"] == "mcp_host"
        calls = [("command_list", {"domain": "document"}),
                 ("command_describe", {"target_command_id": "document.structured-edit"})]
        for name, arguments in calls:
            result = call(name, arguments)
            assert result["isError"] is True
            error = result["structuredContent"]["error"]
            assert error["code"] == "interface_changed"
            assert error["effects"] == "none" and error["retryable"] is False
            assert error["next_action"] == pending["recovery"]
            assert "in flight" not in error["message"]
        # An unaffected call and another Core refresh do not acknowledge discovery.
        unaffected = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert unaffected["isError"] is False, unaffected
        notice = unaffected["structuredContent"]["warnings"]
        assert len(notice) == 1 and notice[0]["code"] == "follow_up_required"
        assert "command_describe, command_list" in notice[0]["message"]
        assert "Do not replay" in notice[0]["message"]
        assert json.loads(unaffected["content"][0]["text"]) == unaffected["structuredContent"]
        quiet = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert quiet["structuredContent"]["warnings"] == []
        assert call("brain_proxy_refresh")["structuredContent"]["result"]["next_action"] == "rediscover_tools"
        assert call("artefact_read", {"reference": "Projects/Command Fixture.md"})["structuredContent"]["warnings"] == []
        cursor = None
        while True:
            page = request("tools/list", {"cursor": cursor} if cursor else {})
            cursor = page.get("nextCursor")
            if not cursor:
                break
        assert status()["interface"]["tool_discovery"]["state"] == "current"
        assert status()["next_action"] is None
        for name, arguments in calls:
            result = call(name, arguments)
            assert result["isError"] is False
            assert result["structuredContent"]["command_version"] == 6
    finally:
        process.terminate()
        process.wait(timeout=5)
