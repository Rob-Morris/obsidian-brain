"""Reserved result-session controls never enter opt-in body diagnostics."""
import json
import sys

import pytest

from brain_mcp import proxy
from brain_mcp._result_content import result_text_wire
from brain_mcp._proxy_handoff import RawLineReader


@pytest.fixture
def capture(tmp_path, monkeypatch):
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core/VERSION").write_text("0.0.0\n")
    logger = proxy._operational_log.OperationalLogger(tmp_path, "proxy")
    monkeypatch.setattr(proxy._operational_log, "current_logger", lambda: logger)

    def record(raw, direction="client_to_child"):
        proxy._capture_body(direction, "tools/call" if direction == "client_to_child" else None, raw)
        logger.close(exit_code=0)
        path = proxy._operational_log.diagnostics_directory(tmp_path) / "debug-bodies.log"
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(lines) == 1
        return lines[0], path.read_bytes()

    yield record
    logger.close(exit_code=0)


@pytest.mark.parametrize("credential", ["synthetic-private-credential", {"invalid": "synthetic-private-credential"},
                                         ["synthetic-private-credential"], None, 7, "\ud800"])
def test_request_capture_redacts_even_invalid_reserved_values(capture, credential):
    business = {"content": "Ordinary colour 雪, quotes \" and backslash \\.",
                "metadata": {"brain_session": "business field", "credential": "business value"}}
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "document_write-body", "arguments": {"brain_session": credential, **business}}}
    raw = json.dumps(request).encode()
    record, persisted = capture(raw)
    logged = json.loads(record["body"])
    arguments = logged["params"]["arguments"]
    assert arguments["brain_session"] == "[redacted transport control]"
    assert {key: arguments[key] for key in business} == business
    assert b"synthetic-private-credential" not in persisted
    assert request["params"]["arguments"]["brain_session"] == credential
    assert raw == json.dumps(request).encode()


@pytest.mark.parametrize("structured", [True, False])
@pytest.mark.parametrize("annotated", [True, False])
@pytest.mark.parametrize("credential", ["synthetic-private-credential", {"invalid": "synthetic-private-credential"}])
def test_response_capture_redacts_structured_and_json_text_copies(capture, structured, annotated, credential):
    business = {"content": "Ordinary response body 雪.",
                "result_session": {"credential": "domain-owned business field"}}
    envelope = {"schema": "brain.command-result/1", "command": "session.start", "status": "ok",
                "result": business, "result_session": {"credential": credential, "instruction": "Keep private."}}
    content = result_text_wire("Human response body", envelope)
    if not annotated:
        for block in content:
            block.pop("annotations")
    result = {"content": content, "isError": False}
    if structured:
        result["structuredContent"] = envelope
    response = {"jsonrpc": "2.0", "id": 1, "result": result}
    raw = json.dumps(response).encode()
    record, persisted = capture(raw, "child_to_client")
    logged = json.loads(record["body"])["result"]
    assistant = json.loads(logged["content"][0]["text"])
    assert assistant["result_session"]["credential"] == "[redacted transport control]"
    assert assistant["result_session"]["instruction"] == "Keep private."
    assert assistant["result"] == business
    if structured:
        assert logged["structuredContent"] == assistant
    assert logged["content"][1]["text"] == "Human response body"
    assert b"synthetic-private-credential" not in persisted
    assert raw == json.dumps(response).encode()


@pytest.mark.parametrize("body", [b'{"params":{"arguments":{"brain_session":"synthetic-private-credential',
                                  b'\xffsynthetic-private-credential', b'["synthetic-private-credential"]'])
def test_unparseable_capture_never_falls_back_to_raw_credentials(capture, body):
    record, persisted = capture(body)
    assert record["body"] == "[body omitted: invalid JSON transport frame]"
    assert b"synthetic-private-credential" not in persisted


def test_ordinary_capture_preserves_raw_business_body(capture):
    frame = {"jsonrpc": "2.0", "id": 1, "result": {"content": [
        {"type": "text", "text": '{"credential":"business value"}'},
        {"type": "text", "text": "Keep this ordinary body exactly. 雪"}]}}
    raw = json.dumps(frame, ensure_ascii=False, indent=2).encode() + b"\n"
    record, _ = capture(raw, "child_to_client")
    assert record["body"] == raw.decode().strip()
    assert json.loads(record["body"]) == frame


def test_capture_redacts_controls_before_log_truncation(capture):
    request = {"params": {"arguments": {"brain_session": "synthetic-private-credential",
                                         "content": "ordinary-body " * 10000}}}
    record, persisted = capture(json.dumps(request).encode())
    assert record["truncated"] is True
    assert "ordinary-body" in record["body"]
    assert "[redacted transport control]" in record["body"]
    assert b"synthetic-private-credential" not in persisted


def test_idle_handoff_excludes_raw_inflight_requests(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._client_protocol = "modern"
    request = {"id": 1, "method": "tools/call", "params": {"name": "session_start", "arguments": {
        "brain_session": "synthetic-private-credential"}}}
    relay._inflight_requests[1] = (request, 0)
    state = relay._transport_state("restart", RawLineReader(0))
    assert state["remainder"] == b""
    assert "synthetic-private-credential" not in json.dumps({k: v for k, v in state.items() if k != "remainder"})
    monkeypatch.setattr(relay, "_transport_state", lambda *a: pytest.fail("busy handoff captured transport state"))
    restart = {"id": 2, "method": "tools/call", "params": {"name": "brain_proxy_restart", "arguments": {}}}
    assert relay._admit_lifecycle(restart, RawLineReader(0)) == "server_busy"
    assert relay._pending_lifecycle is None


def test_malformed_assistant_duplicate_is_omitted_without_echo(capture):
    response = {"id": 1, "result": {"structuredContent": {
        "schema": "brain.command-result/1", "result_session": {
            "credential": "synthetic-private-credential"}, "result": {"content": "Ordinary body"}},
        "content": [{"type": "text", "annotations": {"audience": ["assistant"]},
                     "text": '{"result_session":{"credential":"synthetic-private-credential'},
                    {"type": "text", "annotations": {"audience": ["user"]}, "text": "Ordinary human body"}]}}
    record, persisted = capture(json.dumps(response).encode(), "child_to_client")
    result = json.loads(record["body"])["result"]
    assert result["structuredContent"]["result"]["content"] == "Ordinary body"
    assert result["content"][0]["text"] == "[body omitted: invalid JSON transport frame]"
    assert result["content"][1]["text"] == "Ordinary human body"
    assert b"synthetic-private-credential" not in persisted


def test_invalid_issuance_control_shape_is_redacted(capture):
    envelope = {"schema": "brain.command-result/1", "result_session": "synthetic-private-credential",
                "result": "Ordinary body"}
    response = {"id": 1, "result": {"structuredContent": envelope,
                                    "content": result_text_wire("Ordinary human body", envelope)}}
    record, persisted = capture(json.dumps(response).encode(), "child_to_client")
    result = json.loads(record["body"])["result"]
    assert result["structuredContent"]["result_session"] == "[redacted transport control]"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["result"] == "Ordinary body"
    assert b"synthetic-private-credential" not in persisted


def test_unannotated_duplicate_without_schema_is_redacted_but_business_json_is_preserved(capture):
    envelope = {"result_session": {"credential": "synthetic-private-credential"}, "result": "Ordinary body"}
    business = {"result_session": {"credential": "domain-owned business field"}}
    response = {"id": 1, "result": {"structuredContent": envelope, "content": [
        {"type": "text", "text": json.dumps(envelope)}, {"type": "text", "text": json.dumps(business)}]}}
    record, persisted = capture(json.dumps(response).encode(), "child_to_client")
    result = json.loads(record["body"])["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert json.loads(result["content"][1]["text"]) == business
    assert b"synthetic-private-credential" not in persisted
