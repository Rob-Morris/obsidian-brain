"""Source warning policy stays consistent across final transport fallbacks."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from _command_interface.script import _cli_result_fits, _guard_cli_result
from brain_mcp._result_content import (
    guard_tool_result, has_catalogue_notice, result_text_wire, tool_result_fits,
)


@pytest.mark.parametrize("status", ["ok", "partial", "error"])
@pytest.mark.parametrize("huge", [False, True])
def test_overflow_preserves_source_warnings_and_execution(status, huge):
    private = "private-source-warning-雪\\\""
    warnings = [{"code": "follow_up_required", "message": (
        private * 3000 if huge else "Complete the required source recovery step.")}]
    effects = [{"kind": "document.written", "subject": "Example.md"}]
    source = {"schema": "brain.command-result/1", "command": "document.write",
              "command_version": 1, "status": status, "warnings": warnings,
              "result": {"content": "😀" * 12000}, "committed_effects": effects,
              "delivery": {"result_id": "a" * 64, "view_id": "b" * 32},
              "result_session_file": "/private/session"}
    if status != "ok":
        source["error"] = {"code": "conflict", "effects": "known", "message": "Source error"}
    original = deepcopy(source)
    cli, concise = _guard_cli_result(source, status)
    wire = guard_tool_result({"structuredContent": source,
                              "content": result_text_wire(status, source),
                              "isError": status != "ok"})
    mcp = wire["structuredContent"]
    assert source == original
    assert _cli_result_fits(cli, concise)
    assert tool_result_fits(wire)
    assert json.loads(wire["content"][0]["text"]) == mcp
    assert wire["isError"] == (status != "ok")
    assert cli["result_session_file"] == "/private/session"
    for output in (cli, mcp):
        assert output["status"] == status
        assert output["committed_effects"] == effects
        if status != "ok":
            assert output["error"]["code"] == "conflict"
            assert output["error"]["effects"] == "known"
        assert "brain_proxy_status" not in json.dumps(output)
        if not huge:
            assert output["warnings"] == warnings
        else:
            summary, = output["warnings"]
            assert summary["code"] == "source_warnings_retained"
            assert summary["count"] == 1
            canonical = json.dumps(warnings, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")
            assert summary["sha256"] == sha256(canonical).hexdigest()
            assert "detail=@outcome" in summary["message"]
            assert private not in json.dumps(output, ensure_ascii=False)
    assert cli["warnings"] == mcp["warnings"]


def test_actual_transport_notice_survives_oversized_source_warnings():
    source = {"schema": "brain.command-result/1", "command": "document.write",
              "command_version": 1, "status": "ok", "committed_effects": [],
              "warnings": [{"code": "custom", "message": "private-warning" * 10000},
                           {"code": "follow_up_required", "message":
                            "Brain MCP transport observation: catalogue changed."}],
              "result": {}, "delivery": {"result_id": "a" * 64},
              "result_session": {"credential": "synthetic-private-credential"}}
    result = guard_tool_result({"structuredContent": source,
                               "content": result_text_wire("ok", source), "isError": False})
    assert tool_result_fits(result)
    assert has_catalogue_notice(result)
    assert "private-warning" not in json.dumps(result)
    assert result["structuredContent"]["result_session"] == source["result_session"]
    assert result["structuredContent"]["warnings"][0]["code"] == "source_warnings_retained"


def test_small_required_warning_survives_late_metadata_fallback():
    warnings = [{"code": "follow_up_required", "message": "Required source guidance."}]
    source = {"schema": "brain.command-result/1", "command": "document.write",
              "command_version": 1, "status": "ok", "result": {}, "warnings": warnings,
              "committed_effects": [], "outcome": {"execution": "x" * 20000},
              "delivery": {"result_id": "a" * 64, "change_notice": "x" * 20000}}
    cli, concise = _guard_cli_result(source, "ok")
    wire = guard_tool_result({"structuredContent": source,
                              "content": result_text_wire("ok", source), "isError": False})
    assert _cli_result_fits(cli, concise)
    assert tool_result_fits(wire)
    assert cli["warnings"] == wire["structuredContent"]["warnings"] == warnings
