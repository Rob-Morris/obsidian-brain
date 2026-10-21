"""Final proxy writer budgets and delivery acknowledgement."""
import io
import json
import sys

import pytest

from brain_mcp import proxy
from brain_mcp._result_content import (
    canonical_tool_result_size, encoded_tool_result_size, result_text_wire,
    tool_result_fits,
)


def frame(body, *, status="ok", effects=None):
    envelope = {"schema": "brain.command-result/1", "command": "document.write",
                "command_version": 1, "status": status, "warnings": [],
                "result": body, "committed_effects": effects or [],
                "delivery": {"status": "complete", "result_id": "retained-123"}}
    return {"jsonrpc": "2.0", "id": "correlation" * 4000, "result": {
        "structuredContent": envelope, "content": result_text_wire("Written", envelope),
        "isError": status == "error"}}


def writer(tmp_path, stream):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._output_stream = stream
    return relay


def deliver(relay, response):
    relay._outbound.put(response)
    relay._outbound.put(None)
    relay._writer_thread()


@pytest.mark.parametrize("body", ["ordinary", "😀" * 1400, '\\"\n' * 4000])
def test_actual_writer_dual_budget_and_parity(tmp_path, body):
    stream = io.BytesIO()
    response = frame(body)
    deliver(writer(tmp_path, stream), response)
    result = json.loads(stream.getvalue())["result"]
    assert canonical_tool_result_size(result["structuredContent"]) <= 8000
    assert encoded_tool_result_size(result) <= 32768
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    if body == "ordinary":
        assert result == response["result"]
    else:
        assert result["structuredContent"]["delivery"]["status"] == "unavailable"
        assert result["structuredContent"]["delivery"]["result_id"] == "retained-123"


@pytest.mark.parametrize("status", ["ok", "partial", "error"])
def test_large_committed_effects_preserve_execution_truth(tmp_path, status):
    stream = io.BytesIO()
    effects = [{"subject": "文" * 1000, "effect": "written"}] * 100
    deliver(writer(tmp_path, stream), frame("body", status=status, effects=effects))
    result = json.loads(stream.getvalue())["result"]
    assert tool_result_fits(result)
    assert result["structuredContent"]["status"] == status
    assert result["isError"] == (status == "error")
    assert result["structuredContent"]["committed_effect_summary"]["count"] == 100


class FailedStream(io.BytesIO):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase

    def write(self, data):
        if self.phase == "write":
            raise BrokenPipeError()
        if self.phase == "short":
            return 1
        return super().write(data)

    def flush(self):
        if self.phase == "flush":
            raise OSError("flush failed")
        return super().flush()


@pytest.mark.parametrize("phase", ["write", "short", "flush", "success"])
def test_notice_committed_only_after_write_and_flush(tmp_path, monkeypatch, phase):
    stream = io.BytesIO() if phase == "success" else FailedStream(phase)
    relay = writer(tmp_path, stream)
    relay._proxy_drift = True
    relay._proxy_version_on_disk = "0.10.12"
    monkeypatch.setattr(relay, "_initiate_shutdown", lambda: None)
    response = frame("body")
    prepared = relay._decorate_catalogue_notice(response)
    assert prepared != response
    assert relay._catalogue_notice_generation is None
    deliver(relay, response)
    assert relay._catalogue_notice_generation == (relay._catalogue_generation if phase == "success" else None)


def test_rejected_candidate_only_commits_notice_carried_by_accepted_fallback(tmp_path):
    relay = writer(tmp_path, io.BytesIO())
    relay._proxy_drift = True
    relay._proxy_version_on_disk = "0.10.12"
    response = frame("x" * 7400)
    assert tool_result_fits(response["result"])
    assert relay._decorate_catalogue_notice(response) != response
    assert relay._catalogue_notice_generation is None
    deliver(relay, response)
    assert relay._catalogue_notice_generation == relay._catalogue_generation
    result = json.loads(relay._output_stream.getvalue())["result"]
    assert result["structuredContent"]["delivery"]["status"] == "unavailable"
    assert "brain_proxy_status" in json.dumps(result["structuredContent"]["warnings"])


@pytest.mark.parametrize("result", [{"tools": [{"name": "x" * 40000}]},
                                   {"protocolVersion": "2025-03-26", "capabilities": {}}])
def test_non_tool_protocol_results_unchanged(tmp_path, result):
    stream = io.BytesIO()
    deliver(writer(tmp_path, stream), {"jsonrpc": "2.0", "id": 1, "result": result})
    assert json.loads(stream.getvalue())["result"] == result


def test_proxy_control_descriptor_and_bounded_error(tmp_path):
    from brain_mcp._proxy_controls import STATUS_TOOL, control_response

    for status in ({"state": "ready"}, {"diagnostic": "😀" * 30000}):
        stream = io.BytesIO()
        deliver(writer(tmp_path, stream), control_response(1, STATUS_TOOL, status))
        result = json.loads(stream.getvalue())["result"]
        assert tool_result_fits(result)
        assert result["structuredContent"]["delivery"]["status"] in {"complete", "unavailable"}
        assert result["structuredContent"]["status"] == "ok"
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_metadata_overflow_bounded_without_losing_status(tmp_path):
    response = frame("body", status="partial")
    envelope = response["result"]["structuredContent"]
    envelope["delivery"]["result_id"] = "😀" * 10000
    envelope["result_ref"] = "stable-reference"
    envelope["command"] = "x" * 10000
    response["result"]["content"] = result_text_wire("Written", envelope)
    stream = io.BytesIO()
    deliver(writer(tmp_path, stream), response)
    result = json.loads(stream.getvalue())["result"]
    assert tool_result_fits(result)
    assert result["structuredContent"]["status"] == "partial"
    assert result["structuredContent"]["result_ref"] == "stable-reference"
    assert "Mandatory metadata" in result["structuredContent"]["delivery"]["message"]


def test_accepted_notice_once_and_original_layout_preserved(tmp_path):
    stream = io.BytesIO()
    relay = writer(tmp_path, stream)
    relay._proxy_drift = True
    relay._proxy_version_on_disk = "0.10.12"
    response = frame("body")
    response["result"]["structuredContent"]["delivery"].update(page=3, total_pages=6, total_items=150)
    relay._outbound.put(response)
    deliver(relay, response)
    first, second = [json.loads(line)["result"] for line in stream.getvalue().splitlines()]
    assert len(first["structuredContent"]["warnings"]) == 1
    assert second["structuredContent"]["warnings"] == []
    assert first["structuredContent"]["delivery"] == response["result"]["structuredContent"]["delivery"]
    assert response["result"]["structuredContent"]["warnings"] == []


@pytest.mark.parametrize("limit", [8000, 8001])
def test_exact_canonical_boundary(limit):
    envelope = {"body": ""}
    overhead = canonical_tool_result_size(envelope)
    envelope["body"] = "x" * (limit - overhead)
    result = {"structuredContent": envelope, "content": result_text_wire("", envelope)}
    assert canonical_tool_result_size(envelope) == limit
    assert tool_result_fits(result) == (limit == 8000)


@pytest.mark.parametrize("limit", [32768, 32769])
def test_exact_encoded_tool_value_boundary(limit):
    envelope = {"status": "ok"}
    result = {"structuredContent": envelope, "content": result_text_wire("", envelope)}
    overhead = encoded_tool_result_size(result)
    result["content"][1]["text"] = "x" * (limit - overhead)
    assert encoded_tool_result_size(result) == limit
    assert tool_result_fits(result) == (limit == 32768)


def test_oversize_body_retains_small_effects_and_error(tmp_path):
    response = frame("x" * 10000, status="partial", effects=[{"effect": "written", "subject": "a"}])
    envelope = response["result"]["structuredContent"]
    envelope["error"] = {"code": "write_partial", "effects": "committed"}
    response["result"]["content"] = result_text_wire("Partial", envelope)
    stream = io.BytesIO()
    deliver(writer(tmp_path, stream), response)
    delivered = json.loads(stream.getvalue())["result"]["structuredContent"]
    assert delivered["committed_effects"] == envelope["committed_effects"]
    assert delivered["error"] == envelope["error"]
    assert delivered["status"] == "partial"


@pytest.mark.parametrize("credential", ["synthetic-private-credential-" + "a" * 480, "😀" * 512])
@pytest.mark.parametrize("status", ["ok", "partial", "error"])
@pytest.mark.parametrize("phase", ["success", "write", "flush"])
def test_large_effects_notice_and_session_survive_fallback(tmp_path, monkeypatch, caplog, status, phase, credential):
    stream = io.BytesIO() if phase == "success" else FailedStream(phase)
    relay = writer(tmp_path, stream)
    relay._proxy_drift = True
    relay._proxy_version_on_disk = "0.10.12"
    monkeypatch.setattr(relay, "_initiate_shutdown", lambda: None)
    response = frame("body", status=status, effects=[{"effect": "written", "subject": "文" * 1000}] * 100)
    envelope = response["result"]["structuredContent"]
    # Synthetic caller-only issuance metadata; never use a real credential in tests.
    session = {"credential": credential,
               "instruction": "Keep private and use brain_session for subsequent calls."}
    envelope["result_session"] = session
    envelope["result_ref"] = {"command_id": "result.read", "result_id": "retained-123", "detail": "@outcome"}
    envelope["warnings"] = [{"code": "source_warning", "message": "large source warning " * 2000}]
    response["result"]["content"] = result_text_wire("Written", envelope)
    prepared = relay._decorate_catalogue_notice(response)
    assert relay._catalogue_notice_generation is None
    assert prepared != response
    deliver(relay, response)
    assert session["credential"] not in caplog.text
    assert relay._catalogue_notice_generation == (relay._catalogue_generation if phase == "success" else None)
    if phase != "write":
        result = json.loads(stream.getvalue())["result"]
        delivered = result["structuredContent"]
        assert tool_result_fits(result)
        assert delivered["result_session"] == session
        assert delivered["status"] == status
        assert result["isError"] == (status == "error")
        assert delivered["command"] == "document.write"
        assert delivered["command_version"] == 1
        assert delivered["committed_effect_summary"]["count"] == 100
        assert delivered["result_ref"] == envelope["result_ref"]
        assert delivered["delivery"]["result_id"] == "retained-123"
        warnings = delivered["warnings"]
        assert any(w["code"] == "follow_up_required" and "brain_proxy_status({})" in w["message"] for w in warnings)
        assert any(w["code"] == "source_warnings_retained" and "@outcome" in w["message"] for w in warnings)
        assert json.loads(result["content"][0]["text"]) == delivered


@pytest.mark.parametrize("field", ["command", "command_version"])
def test_invalid_core_identity_is_explicit_without_retyping_status(tmp_path, field):
    response = frame("x" * 10000, status="partial")
    envelope = response["result"]["structuredContent"]
    envelope[field] = "unbounded" * 2000
    response["result"]["content"] = result_text_wire("Partial", envelope)
    stream = io.BytesIO()
    deliver(writer(tmp_path, stream), response)
    result = json.loads(stream.getvalue())["result"]
    assert tool_result_fits(result)
    delivered = result["structuredContent"]
    assert delivered["status"] == "partial"
    assert result["isError"] is False
    assert field not in delivered
    assert field in delivered["metadata_unavailable"]
    assert "sha256" not in json.dumps(delivered)


def test_small_source_warnings_survive_oversize_body(tmp_path):
    response = frame("x" * 10000)
    envelope = response["result"]["structuredContent"]
    envelope["warnings"] = [{"code": "source_warning", "message": "Source observation needs attention."}]
    response["result"]["content"] = result_text_wire("Written", envelope)
    stream = io.BytesIO()
    deliver(writer(tmp_path, stream), response)
    delivered = json.loads(stream.getvalue())["result"]["structuredContent"]
    assert delivered["warnings"] == envelope["warnings"]


def test_invalid_issuance_metadata_not_echoed_or_hashed(tmp_path, caplog):
    response = frame("body")
    envelope = response["result"]["structuredContent"]
    secret = "synthetic-oversize-secret-" * 1000
    envelope["result_session"] = {"credential": secret, "instruction": "Keep private."}
    response["result"]["content"] = result_text_wire("Written", envelope)
    stream = io.BytesIO()
    deliver(writer(tmp_path, stream), response)
    result = json.loads(stream.getvalue())["result"]
    assert tool_result_fits(result)
    assert "result_session" in result["structuredContent"]["metadata_unavailable"]
    assert "result_session" not in result["structuredContent"]
    assert "sha256" not in json.dumps(result)
    assert secret not in stream.getvalue().decode()
    assert secret not in caplog.text


def test_writer_does_not_commit_notice_absent_from_guarded_result(tmp_path, monkeypatch):
    relay = writer(tmp_path, io.BytesIO())
    relay._proxy_drift = True
    relay._proxy_version_on_disk = "0.10.12"
    original_guard = proxy.guard_tool_result

    def without_notice(result):
        guarded = original_guard(result)
        envelope = {**guarded["structuredContent"], "warnings": []}
        return {**guarded, "structuredContent": envelope, "content": result_text_wire("Written", envelope)}

    monkeypatch.setattr(proxy, "guard_tool_result", without_notice)
    deliver(relay, frame("body"))
    assert relay._catalogue_notice_generation is None
    delivered = json.loads(relay._output_stream.getvalue())["result"]
    assert delivered["structuredContent"]["warnings"] == []
