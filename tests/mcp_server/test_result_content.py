"""MCP dual-audience text projection."""

from __future__ import annotations

import json

from brain_mcp._result_content import result_text_content, result_text_wire
from brain_mcp.proxy import _decorate_with_drift_note, _human_text_index


def test_content_only_hosts_receive_the_structured_envelope_as_json_text():
    """Grok 1.0.30 forwarded only MCP text to the model (`OkayOutput`).

    MCP 2025-06-18 says a tool that returns structuredContent SHOULD also
    serialize that JSON in a TextContent block. Put that block first so a
    host that takes only content[0] still receives the envelope.
    """

    payload = {
        "schema": "brain.command-result/1",
        "command": "session.start",
        "status": "ok",
        "result": {"brain_core_version": "0.64.2", "version": "3"},
    }
    model_visible = result_text_content("session.start: ok", payload)[0].text
    received = json.loads(model_visible)
    assert received == payload
    assert received["result"]["brain_core_version"] == "0.64.2"


def test_assistant_json_precedes_user_one_liner():
    payload = {"schema": "brain.command-result/1", "command": "command.list", "status": "ok"}
    assistant, user = result_text_content("command.list: ok", payload)

    assert assistant.annotations.audience == ["assistant"]
    assert json.loads(assistant.text) == payload
    assert user.annotations.audience == ["user"]
    assert user.text == "command.list: ok"
    wire = result_text_wire("command.list: ok", payload)
    assert wire[0]["annotations"]["audience"] == ["assistant"]
    assert wire[1]["annotations"]["audience"] == ["user"]


def test_drift_note_appends_to_user_text_not_assistant_json():
    payload = {"status": "ok"}
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": result_text_wire("command.list: ok", payload),
            "structuredContent": payload,
        },
    }

    decorated = _decorate_with_drift_note(response, "0.9.0", "0.9.1")
    content = decorated["result"]["content"]

    assert json.loads(content[0]["text"]) == payload
    assert _human_text_index(content) == 1
    assert content[1]["text"].startswith("command.list: ok")
    assert "0.9.0 → 0.9.1" in content[1]["text"]
