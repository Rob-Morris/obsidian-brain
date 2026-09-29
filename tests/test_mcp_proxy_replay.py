"""Protocol replay and owned outcome recovery without repeating accepted operations."""

import json
import textwrap
import time
from dataclasses import replace
import pytest
from brain_mcp import proxy as proxy_mod
from brain_mcp._command_adapter import application_interface_header
from _application.registry import current_application_catalogue

from proxy_test_support import (
    _FakeChild,
    _ReadableFakeChild,
    _drift_then_echo_server_script,
    _find_by_id,
    _find_notification,
    _launch_proxy,
    _make_inprocess_proxy,
    _make_inprocess_proxy_with_real_threads,
    _make_jsonrpc,
    _read_all_responses,
    _read_responses,
    _read_until_id,
    _wait_for,
    _with_interface_discovery,
    _write_vault,
)

pytestmark = pytest.mark.slow


def _receipt_lookup_response(
    query_id: str,
    record,
    *,
    state: str = "committed",
    found: bool = True,
) -> dict:
    reference = {"invocation_id": record.invocation_id}
    receipt = None
    if found:
        effects = (
            [{"kind": "artefact.created", "subject": "Designs/Example.md"}]
            if state in {"committed", "known_partial"}
            else []
        )
        receipt = {
            "reference": reference,
            "command_id": record.command_id,
            "command_version": record.command_version,
            "state": state,
            "recorded_at": "2026-08-10T10:00:00+10:00",
            "committed_effects": effects,
        }
    return {
        "jsonrpc": "2.0",
        "id": query_id,
        "result": {
            "content": [{"type": "text", "text": "invocation.read: ok"}],
            "structuredContent": {
                "schema": "brain.command-result/1",
                "command": "invocation.read",
                "command_version": 3,
                "status": "ok",
                "warnings": [],
                "result": {
                    "reference": reference,
                    "state": "found" if found else "still_unknown",
                    "intent": ({"reference": reference, "command_id": record.command_id,
                                "command_version": record.command_version, "recorded_at": "2026-08-10T09:59:00+10:00",
                                "basis": "initial", "generation": "test-generation", "source": "host-request",
                                "grant_id": None, "operation_id": None, "operation_digest": None,
                                "request_id": None, "permission_change": None} if found else None),
                    "outcome": ({"receipt": receipt, "execution": {"committed": "succeeded", "none": "succeeded",
                                 "known_partial": "partial", "unknown": "unknown"}[state], "config_revision": None} if found else None),
                },
                "committed_effects": [],
            },
            "isError": False,
        },
    }


def _progress_then_drift_server_script(tmp_path) -> str:
    """
    Server: on run 1, replies to the first tools/call with a "starting" progress
    CallToolResult (isError=true) — a real JSON-RPC response carrying the
    request id, matching the shape `_fmt_progress` emits during warmup — then
    immediately exits 10 to simulate version drift. On run 2 (detected via a
    marker file), echoes normally.
    """
    marker = str(tmp_path / "progress_drift.marker")
    path = str(tmp_path / "progress_then_drift_server.py")
    script = textwrap.dedent(f"""\
        import sys, json, os

        marker_path = {marker!r}
        first_run = not os.path.exists(marker_path)
        if first_run:
            open(marker_path, "w").close()

        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            method = obj.get("method", "")
            msg_id = obj.get("id")
            if method == "initialize":
                resp = {{"jsonrpc": "2.0", "id": msg_id,
                         "result": {{"protocolVersion": "2024-11-05",
                                    "capabilities": {{}},
                                    "serverInfo": {{"name": "progress-drift", "version": "0.0.1"}}}}}}
                print(json.dumps(resp), flush=True)
            else:
                if first_run:
                    progress = {{"status": "starting", "tool": "ping"}}
                    resp = {{"jsonrpc": "2.0", "id": msg_id,
                             "result": {{
                                 "content": [{{"type": "text", "text": json.dumps(progress)}}],
                                 "isError": True,
                             }}}}
                    print(json.dumps(resp), flush=True)
                    sys.exit(10)
                else:
                    resp = {{"jsonrpc": "2.0", "id": msg_id,
                             "result": {{"content": [{{"type": "text", "text": "ok after restart"}}]}}}}
                    print(json.dumps(resp), flush=True)
    """)
    with open(path, "w") as f:
        f.write(_with_interface_discovery(script))
    return path


class TestVersionDriftReplay:
    """Transport compatibility never grants permission to repeat an accepted operation."""

    def test_granular_call_records_proxy_owned_identity_before_dispatch(
        self, tmp_path, monkeypatch
    ):
        proxy, _sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        header = application_interface_header(current_application_catalogue())
        with proxy._interface_lock:
            proxy._interface_header = header
        raw = json.loads(
            _make_jsonrpc(
                "tools/call",
                id="granular-1",
                params={
                    "name": "artefact_create",
                    "arguments": {"type": "living/wiki", "title": "Example"},
                },
            )
        )

        forwarded, record = proxy._prepare_interface_call(raw)

        assert record.raw_request == raw
        assert record.projected_tool == "artefact_create"
        assert record.command_id == "artefact.create"
        assert record.header_fingerprint == header.fingerprint
        assert record.invocation_id.startswith("mcp-")
        assert forwarded["params"]["_meta"] == {
            "brainInvocation": {"invocationId": record.invocation_id}
        }

    def test_unadvertised_legacy_tool_refuses_before_child_dispatch(
        self, tmp_path, monkeypatch
    ):
        proxy, _sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        with proxy._interface_lock:
            proxy._interface_header = application_interface_header(
                current_application_catalogue()
            )
        raw = json.loads(
            _make_jsonrpc(
                "tools/call",
                id="legacy-1",
                params={"name": "brain_create", "arguments": {}},
            )
        )

        with pytest.raises(ValueError, match="not advertised"):
            proxy._prepare_interface_call(raw)

        assert proxy._accepted_calls == {}

    def test_compatible_granular_call_is_not_replayed(
        self, tmp_path, monkeypatch
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        replacement = _FakeChild()
        with proxy._child_lock:
            proxy._child = replacement
        header = application_interface_header(current_application_catalogue())
        with proxy._interface_lock:
            proxy._interface_header = header
        raw = json.loads(
            _make_jsonrpc(
                "tools/call",
                id=201,
                params={"name": "artefact_read", "arguments": {"path": "Example"}},
            )
        )
        forwarded, record = proxy._prepare_interface_call(raw)

        proxy._replay_requests([forwarded], {201: record})

        assert replacement.sent == []
        assert proxy._accepted_calls == {}
        assert sent_to_client[-1]["result"]["structuredContent"]["error"]["code"] == "command_outcome_unknown"

    @pytest.mark.parametrize("send_fails", [False, True])
    def test_protocol_replay_tracking_precedes_response_and_cleans_up_send_failure(
        self, tmp_path, monkeypatch, send_fails
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        with proxy._interface_lock:
            proxy._interface_header = application_interface_header(
                current_application_catalogue()
            )
        forwarded = {"jsonrpc": "2.0", "id": 201, "method": "tools/list", "params": {}}
        response = {"jsonrpc": "2.0", "id": 201, "result": {"ok": True}}
        child = _ReadableFakeChild([json.dumps(response).encode()])
        with proxy._child_lock:
            proxy._child = child
        monkeypatch.setattr(proxy_mod.sys, "platform", "win32")

        def send_and_stop(obj):
            sent_to_client.append(obj)
            proxy._shutdown = True

        def send_with_immediate_response(obj):
            assert proxy._accepted_calls == {}
            assert 201 in proxy._inflight_requests
            assert 201 in proxy._frame_seqs
            if send_fails:
                raise BrokenPipeError()
            # Force the ordinary reader to consume the response before send
            # returns, without depending on thread scheduling or sleeps.
            proxy._reader_thread()

        monkeypatch.setattr(proxy, "_send_to_client", send_and_stop)
        monkeypatch.setattr(child, "send", send_with_immediate_response)

        proxy._replay_requests([forwarded], {})

        assert proxy._inflight_requests == {}
        assert proxy._accepted_calls == {}
        assert proxy._frame_seqs == {}
        assert len(sent_to_client) == 1
        assert ("error" in sent_to_client[0]) == send_fails

    def test_incompatible_granular_replay_fails_closed_without_child_dispatch(
        self, tmp_path, monkeypatch
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        replacement = _FakeChild()
        with proxy._child_lock:
            proxy._child = replacement
        header = application_interface_header(current_application_catalogue())
        with proxy._interface_lock:
            proxy._interface_header = header
        raw = json.loads(
            _make_jsonrpc(
                "tools/call",
                id=202,
                params={"name": "artefact_read", "arguments": {"path": "Example"}},
            )
        )
        forwarded, record = proxy._prepare_interface_call(raw)
        changed = replace(
            header,
            tools=tuple(
                (
                    (
                        name,
                        replace(mapping, command_version=mapping.command_version + 1),
                    )
                    if name == "artefact_read"
                    else (name, mapping)
                )
                for name, mapping in header.tools
            ),
        )
        with proxy._interface_lock:
            proxy._interface_header = changed

        proxy._replay_requests([forwarded], {202: record})

        assert replacement.sent == []
        result = next(message for message in sent_to_client if message.get("id") == 202)
        error = result["result"]["structuredContent"]["error"]
        assert error["code"] == "command_outcome_unknown"
        assert error["retryable"] is False
        assert error["outcome_reference"] == {"invocation_id": record.invocation_id}

    @pytest.mark.parametrize("accepted", [{}, {203: object()}])
    def test_missing_or_invalid_accepted_record_refuses_mapped_replay(
        self, tmp_path, monkeypatch, accepted
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        replacement = _FakeChild()
        with proxy._child_lock:
            proxy._child = replacement
        header = application_interface_header(current_application_catalogue())
        with proxy._interface_lock:
            proxy._interface_header = header
        request = json.loads(
            _make_jsonrpc(
                "tools/call",
                id=203,
                params={"name": "artefact_read", "arguments": {"path": "Example"}},
            )
        )

        proxy._replay_requests([request], accepted)

        assert replacement.sent == []
        result = next(message for message in sent_to_client if message.get("id") == 203)
        reason = result["result"]["structuredContent"]["error"]["details"]["reason"]
        assert reason == (
            "accepted_call_missing" if accepted == {} else "accepted_call_invalid"
        )


class TestUnexpectedChildOutcomeSafety:
    """Every accepted call uses owned outcome recovery without semantic replay."""

    @staticmethod
    def _accepted(proxy, *, request_id: int, tool: str, arguments: dict):
        header = application_interface_header(current_application_catalogue())
        with proxy._interface_lock:
            proxy._interface_header = header
        raw = json.loads(
            _make_jsonrpc(
                "tools/call",
                id=request_id,
                params={"name": tool, "arguments": arguments},
            )
        )
        forwarded, record = proxy._prepare_interface_call(raw)
        return header, forwarded, record

    @pytest.mark.parametrize("found", [True, False])
    def test_read_only_orphan_queries_owned_outcome_without_replay(self, tmp_path, monkeypatch, found):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        header, forwarded, record = self._accepted(proxy, request_id=301, tool="artefact_read",
                                                   arguments={"path": "Designs/Example.md"})
        replacement = _FakeChild()
        monkeypatch.setattr(proxy, "_read_internal_response", lambda _child, query_id, _timeout:
                            _receipt_lookup_response(query_id, record, state="none", found=found))
        proxy._resolve_owned_orphan(replacement, record, header, None)
        assert forwarded not in replacement.sent
        assert len(replacement.sent) == 1
        assert replacement.sent[0]["params"]["name"] == "invocation_read"
        result = sent_to_client[-1]["result"]["structuredContent"]
        if found:
            assert result["outcome"]["execution"] == "succeeded"
            assert result["outcome"]["receipt"]["state"] == "none"
            assert result["retryable"] is False
        else:
            assert result["error"]["code"] == "command_outcome_unknown"
            assert result["error"]["effects"] == "none"
            assert result["error"]["retryable"] is False

    def test_crash_after_mutation_acceptance_queries_receipt_without_replay(
        self, tmp_path, monkeypatch
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        _header, forwarded, record = self._accepted(
            proxy,
            request_id=302,
            tool="artefact_create",
            arguments={"type": "living/wiki", "title": "Example"},
        )
        with proxy._inflight_lock:
            proxy._inflight_requests[302] = (forwarded, time.monotonic())
            proxy._accepted_calls[302] = record

        assert proxy._signal_recovery(1)
        replacement = _FakeChild()

        def receipt_response(_child, query_id, _timeout):
            return _receipt_lookup_response(query_id, record)

        monkeypatch.setattr(proxy, "_read_internal_response", receipt_response)
        proxy._resolve_pending_unexpected(replacement)

        assert len(replacement.sent) == 1
        query = replacement.sent[0]["params"]
        assert query["name"] == "invocation_read"
        assert query["arguments"] == {"invocation_id": record.invocation_id}
        assert query["_meta"]["brainInvocation"]["invocationId"].startswith("mcp-")
        assert "io.modelcontextprotocol/protocolVersion" not in query["_meta"]
        assert forwarded not in replacement.sent
        result = sent_to_client[-1]["result"]["structuredContent"]
        assert result["schema"] == "brain.proxy-outcome-resolution/1"
        assert result["status"] == "resolved"
        assert result["outcome"]["receipt"]["state"] == "committed"
        assert result["retryable"] is False

    @pytest.mark.parametrize("found,state", [(False, "committed"), (True, "unknown")])
    def test_inconclusive_mutation_receipt_returns_queryable_non_retryable_unknown(
        self, tmp_path, monkeypatch, found, state
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        _header, _forwarded, record = self._accepted(
            proxy,
            request_id=303,
            tool="artefact_delete",
            arguments={"path": "Designs/Example.md"},
        )
        replacement = _FakeChild()

        def receipt_response(_child, query_id, _timeout):
            return _receipt_lookup_response(
                query_id,
                record,
                found=found,
                state=state,
            )

        monkeypatch.setattr(proxy, "_read_internal_response", receipt_response)
        proxy._resolve_owned_orphan(
            replacement,
            record,
            proxy._interface_header,
            None,
        )

        result = sent_to_client[-1]["result"]["structuredContent"]
        assert result["status"] == "error"
        assert result["error"]["code"] == "command_outcome_unknown"
        assert result["error"]["effects"] == "unknown"
        assert result["error"]["retryable"] is False
        assert result["error"]["outcome_reference"] == {
            "invocation_id": record.invocation_id
        }
        assert result["error"]["next_action"] == {
            "command_id": "invocation.read",
            "arguments": [
                {"name": "invocation_id", "value": record.invocation_id}
            ],
        }

    def test_contradictory_receipt_is_inconclusive_and_never_dispatches_mutation(
        self, tmp_path, monkeypatch
    ):
        proxy, sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [])
        _header, forwarded, record = self._accepted(
            proxy,
            request_id=304,
            tool="artefact_create",
            arguments={"type": "living/wiki", "title": "Example"},
        )
        replacement = _FakeChild()

        def receipt_response(_child, query_id, _timeout):
            response = _receipt_lookup_response(query_id, record)
            response["result"]["structuredContent"]["result"]["outcome"]["receipt"][
                "command_version"
            ] += 1
            return response

        monkeypatch.setattr(proxy, "_read_internal_response", receipt_response)
        proxy._resolve_owned_orphan(
            replacement,
            record,
            proxy._interface_header,
            None,
        )

        assert forwarded not in replacement.sent
        result = sent_to_client[-1]["result"]["structuredContent"]
        assert result["error"]["code"] == "command_outcome_unknown"
        assert result["error"]["details"] == {
            "reference": {"invocation_id": record.invocation_id}
        }


class TestVersionDriftReplayIntegration:
    """Existing end-to-end drift replay remains correct under protocol binding."""

    def test_drift_triggering_request_gets_success(self, tmp_path):
        """
        The request that causes exit(10) should be replayed to the restarted child,
        so the client gets a success response — not an error.
        """
        _write_vault(tmp_path)
        server_script = _drift_then_echo_server_script(tmp_path)
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_BACKOFF": "0,0,0,0,0"})
        try:
            # Initialize
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # Send request that will trigger version drift (exit code 10)
            proc.stdin.write(_make_jsonrpc("ping", id=2, params={"name": "anything"}))
            proc.stdin.flush()

            # Read until the replayed response arrives. A restart can leave a
            # noticeable gap after list_changed before the success response.
            all_msgs = _read_until_id(proc, 2, timeout=15.0)

            # The triggering request (id=2) should get a SUCCESS response via replay
            resp = _find_by_id(all_msgs, 2)
            assert resp is not None, (
                f"No response with id=2 after drift replay. Got: {all_msgs}"
            )
            assert "result" in resp, (
                f"Expected success for replayed request id=2, got error: {resp}"
            )
            assert resp["result"]["content"][0]["text"] == "ok after restart"

            # Should also get the list_changed notification
            notif = _find_notification(all_msgs, "notifications/tools/list_changed")
            assert notif is not None, (
                f"Expected notifications/tools/list_changed. Got: {all_msgs}"
            )

        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_already_responded_request_not_replayed_on_drift(
        self, tmp_path, monkeypatch
    ):
        """Drain/replay must only act on entries still in `_inflight_requests`.

        The end-to-end invariant is that a request the client has already
        seen answered (success, error, or any progress payload) is not
        replayed on drift. That invariant has two halves: the reader pops
        on every forwarded response, and drain/replay acts only on what
        remains. This test pins the drain/replay half by simulating a
        post-pop state directly; the reader-pop half is not exercised here.
        """
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(
            tmp_path, monkeypatch,
        )
        proxy._backoff_schedule = [0]

        child = _FakeChild()
        replacement = _FakeChild()
        with proxy._child_lock:
            proxy._child = child

        responded_id, inflight_id = 101, 102
        responded_req = json.loads(
            _make_jsonrpc("ping", id=responded_id, params={"name": "ping_a"})
        )
        inflight_req = json.loads(
            _make_jsonrpc("ping", id=inflight_id, params={"name": "ping_b"})
        )
        with proxy._inflight_lock:
            proxy._inflight_requests[responded_id] = (responded_req, time.monotonic())
            proxy._inflight_requests[inflight_id] = (inflight_req, time.monotonic())

        # Simulate the reader thread forwarding a response (any kind — success,
        # error, or progress payload) for the responded request. The pop is
        # the structural invariant under test.
        with proxy._inflight_lock:
            proxy._inflight_requests.pop(responded_id)

        def fake_start_child() -> bool:
            with proxy._child_lock:
                proxy._child = replacement
            proxy._child_ready.set()
            return True

        monkeypatch.setattr(proxy, "_start_child", fake_start_child)
        proxy._start_recovery_loop()

        try:
            assert proxy._signal_recovery(10, child=child), (
                "drift signal should have claimed recovery work"
            )

            assert _wait_for(lambda: proxy._get_child() is replacement, timeout=1.0), (
                "recovery thread did not swap in the replacement child"
            )
            assert _wait_for(lambda: len(replacement.sent) >= 1, timeout=1.0), (
                "expected the still-in-flight request to be replayed"
            )
            assert _wait_for(lambda: proxy._pending_replay == [], timeout=1.0), (
                "pending replay queue should have drained after replay"
            )

            replayed_ids = [obj.get("id") for obj in replacement.sent]
            assert inflight_id in replayed_ids, (
                f"in-flight request id={inflight_id} should have been replayed. "
                f"Replacement child saw: {replayed_ids}"
            )
            assert responded_id not in replayed_ids, (
                f"already-responded request id={responded_id} must not be replayed. "
                f"Replacement child saw: {replayed_ids}"
            )

            client_ids = [obj.get("id") for obj in sent_to_client]
            assert responded_id not in client_ids, (
                f"client must not receive any new message for the already-responded "
                f"request id={responded_id}. Saw: {sent_to_client}"
            )
        finally:
            proxy._initiate_shutdown()
            recovery = proxy._recovery_thread_handle
            if recovery is not None and recovery.is_alive():
                recovery.join(timeout=2.0)

    def test_progress_response_then_drift_no_replay(self, tmp_path):
        """End-to-end pin of the full chain through real subprocess machinery:
        reader pops on a progress-shaped response (isError=true CallToolResult
        with status="starting"), drain excludes popped, replay does not
        resurrect. The realistic regression is the reader skipping the pop for
        progress-shaped responses; that bug would produce a duplicate response
        for id=2 after drift recovery here.
        """
        _write_vault(tmp_path)
        server_script = _progress_then_drift_server_script(tmp_path)
        proc = _launch_proxy(tmp_path, server_script,
                             extra_env={"BRAIN_PROXY_BACKOFF": "0,0,0,0,0"})
        try:
            proc.stdin.write(_make_jsonrpc(
                "initialize", id=1,
                params={"protocolVersion": "2024-11-05",
                        "clientInfo": {"name": "test", "version": "0"}},
            ))
            proc.stdin.flush()
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert init_msgs and init_msgs[0].get("id") == 1

            # Server returns "starting" progress for id=2, then exits 10.
            proc.stdin.write(_make_jsonrpc("ping", id=2,
                                           params={"name": "ping"}))
            proc.stdin.flush()
            progress_msgs = _read_until_id(proc, 2, timeout=5.0)
            progress_resp = _find_by_id(progress_msgs, 2)
            assert progress_resp is not None, (
                f"Expected progress response for id=2. Got: {progress_msgs}"
            )
            assert progress_resp["result"].get("isError") is True
            assert "starting" in progress_resp["result"]["content"][0]["text"]

            # Drain anything the proxy emits during/after drift recovery.
            # list_changed proves recovery completed; the absence of any further
            # id=2 message proves replay did not resurrect the popped request.
            post_recovery = _read_all_responses(
                proc, timeout=5.0, idle=0.5, max_count=20,
            )

            assert _find_notification(
                post_recovery, "notifications/tools/list_changed"
            ) is not None, (
                f"Expected list_changed notification proving recovery "
                f"completed. Got: {post_recovery}"
            )
            id2_extras = [m for m in post_recovery if m.get("id") == 2]
            assert id2_extras == [], (
                f"id=2 was already answered with the 'starting' progress "
                f"response; replay must not resurrect it. Got extra "
                f"responses: {id2_extras}"
            )
        finally:
            proc.terminate()
            proc.wait(timeout=5)


@pytest.mark.parametrize('fault', ['missing_intent', 'foreign_source', 'wrong_command', 'wrong_operation',
                                   'false_none_effect', 'missing_final', 'rpc_error', 'tool_error'])
def test_owned_recovery_rejects_inconsistent_proof_without_replay(tmp_path, monkeypatch, fault):
    proxy, responses = _make_inprocess_proxy(tmp_path, monkeypatch, [])
    header, _forwarded, record = TestUnexpectedChildOutcomeSafety._accepted(
        proxy, request_id=501, tool='artefact_read', arguments={'path': 'Designs/Example.md'})
    child = _FakeChild()

    def lookup(_child, query_id, _timeout):
        value = _receipt_lookup_response(query_id, record, state='none')
        payload = value['result']['structuredContent']['result']
        if fault == 'missing_intent': payload['intent'] = None
        elif fault == 'foreign_source': payload['intent']['source'] = 'cli-request'
        elif fault == 'wrong_command': payload['intent']['command_id'] = 'artefact.create'
        elif fault == 'wrong_operation': payload['intent']['basis'] = 'operation'
        elif fault == 'false_none_effect': payload['outcome']['receipt']['committed_effects'] = [{'kind': 'write', 'subject': 'changed'}]
        elif fault == 'missing_final': payload['outcome'] = None
        elif fault == 'rpc_error': value['error'] = {'code': -1, 'message': 'contradiction'}
        elif fault == 'tool_error': value['result']['isError'] = True
        return value

    monkeypatch.setattr(proxy, '_read_internal_response', lookup)
    try:
        proxy._resolve_owned_orphan(child, record, header, None)
        assert len(child.sent) == 1 and child.sent[0]['params']['name'] == 'invocation_read'
        error = responses[-1]['result']['structuredContent']['error']
        assert error['code'] == 'command_outcome_unknown'
        assert error['effects'] == 'none' and error['retryable'] is False
    finally:
        proxy._initiate_shutdown()
