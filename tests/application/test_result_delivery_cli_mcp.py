"""Pipe/subprocess acceptance of private result sessions and complete delivery.

Uses installed disposable vaults and the real SDK, composer, adapter and store.
Only bootstrap dependency-tier admission/readiness is a declared fixture
surrogate. This is not public-proxy, native-host or runtime certification.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import anyio
import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from _common import document_revision_at
from _application.registry import current_application_catalogue
from _command_interface.profiles import builtin_profile_allow_lists

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER = REPO_ROOT / "tests/fixtures/result_delivery_stdio_server.py"
TARGET = "Designs/project~command-fixture/Command Fixture Design.md"


@pytest.fixture
def delivery_vault(command_vault_clone, tmp_path):
    clone = command_vault_clone
    root = clone.vault_root
    home = tmp_path / "isolated-home"
    home.mkdir()
    # Whitelist process state: no inherited Brain keys, owner channels, host
    # configuration, debug-body logging or user home can reach either child.
    env = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ}
    env.update(clone.environment)
    env.update(HOME=str(home), USERPROFILE=str(home),
               XDG_STATE_HOME=str(home / "state"), XDG_CACHE_HOME=str(home / "cache"),
               PATH=str(Path(sys.executable).parent), PYTHONDONTWRITEBYTECODE="1",
               RESULT_DELIVERY_TEST_VAULT=str(root))
    # Configure this disposable Brain's test principal using the real shared
    # permission configuration. Do not substitute an authorisation context or
    # grant consent in the adapter. Shipped default allow-list parity is a
    # separate integration concern, already reported to the source owner.
    shared = root / ".brain/config.yaml"
    configuration = yaml.safe_load(shared.read_text()) if shared.exists() else {}
    configuration.setdefault("vault", {}).setdefault("profiles", {})["operator"] = {
        "allow": list(builtin_profile_allow_lists(current_application_catalogue())["operator"]),
    }
    shared.write_text(yaml.safe_dump(configuration, default_flow_style=None, width=10000), encoding="utf-8")
    # Recorded ready state prevents detached warm-up/provisioning. It is a
    # readiness surrogate, not evidence of a certified managed installation.
    (root / ".brain/local/runtime-status.json").write_text(json.dumps({
        "schema": "brain.runtime-status/1",
        "core_version": (root / ".brain-core/VERSION").read_text().strip(),
        "run_id": "result-delivery-test-ready", "state": "ready", "phase": None,
        "components": {"router": "ready", "lexical": "ready", "semantic": "disabled"},
        "retry_after_ms": None, "started_at": "2026-10-21T00:00:00+00:00", "last_error": None,
    }), encoding="utf-8")
    preferences = "\n".join(f"Mandatory preference {i:04}: use colour and preserve 雪, quotes \" and \\." for i in range(420))
    gotchas = "\n".join(f"Mandatory gotcha {i:04}: never omit this guidance." for i in range(120))
    user = root / "_Config/User"
    user.mkdir(parents=True, exist_ok=True)
    (user / "preferences-always.md").write_text(preferences + "\n", encoding="utf-8")
    (user / "gotchas.md").write_text(gotchas + "\n", encoding="utf-8")
    return root, env, preferences, gotchas


@asynccontextmanager
async def mcp_client(env):
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    with anyio.fail_after(45):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session


async def mcp_call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    frame = result.structured_content
    assistant = [block for block in result.content if getattr(block, "annotations", None)
                 and "assistant" in (block.annotations.audience or [])]
    assert len(assistant) == 1
    assert json.loads(assistant[0].text) == frame
    assert len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode()) <= 8000
    assert len(json.dumps(result.model_dump(by_alias=True, exclude_none=True)).encode()) <= 32768
    return frame


def error_summary(frame):
    error = frame.get("error") or {}
    return error.get("code"), error.get("message")


def text_content(payload):
    assert "fragments" not in payload
    return payload["content"]


def assert_complete_text(frames, expected):
    offset = 0
    total_pages = frames[0]["delivery"]["total_pages"]
    revision = frames[0]["result"].get("revision")
    for number, frame in enumerate(frames, 1):
        payload = frame["result"]
        chunk = text_content(payload)
        assert "range" not in payload and "next_cursor" not in payload
        assert payload["content_range"] == {
            "start": offset, "end": offset + len(chunk), "total": len(expected),
        }
        assert payload.get("revision") == revision
        delivery = frame["delivery"]
        assert delivery["page"] == number
        assert delivery["total_pages"] == total_pages
        assert delivery["has_more"] == (number < total_pages)
        assert delivery["status"] == ("truncated" if number < total_pages else "complete")
        assert delivery["remaining_pages"] == total_pages - number
        assert delivery["unit"] == "bytes"
        assert delivery["total_items"] == len(expected.encode("utf-8"))
        assert delivery.get("fragments") is None
        offset += len(chunk)
    assert len(frames) == total_pages
    assert "".join(text_content(frame["result"]) for frame in frames) == expected
    assert offset == len(expected)


async def mcp_window(session, first, credential):
    """Follow the single generic delivery continuation to completion."""
    assert first["status"] == "ok", first.get("error")
    assert first["delivery"]["availability"] == "available", (
        first["delivery"]["reason"], first["delivery"]["message"])
    frames = [first]
    for _ in range(100):
        cursor = frames[-1]["delivery"]["continuation"]
        if cursor is None:
            break
        frames.append(await mcp_call(session, "result_read", {
            "brain_session": credential, "result_id": first["delivery"]["result_id"],
            "continuation": cursor,
        }))
        assert frames[-1]["status"] == "ok", error_summary(frames[-1])
        assert "result_session" not in frames[-1]
    else:
        pytest.fail("generic delivery did not finish within the test work bound")
    pages = [frame["result"] for frame in frames]
    ranges = [frame["delivery"] for frame in frames]
    assert [item["page"] for item in ranges] == list(range(1, len(frames) + 1))
    assert not ranges[-1].get("has_more_outside_selection", False)
    return "".join(text_content(page) for page in pages), pages[-1], frames


def test_mcp_bootstrap_single_pager_matches_complete_mandatory_mirror(delivery_vault):
    root, env, preferences, gotchas = delivery_vault

    async def exercise():
        async with mcp_client(env) as session:
            tools = await session.list_tools()
            schema = next(tool.input_schema for tool in tools.tools if tool.name == "result_read")
            continuation_schema = schema["properties"]["continuation"]
            assert set(continuation_schema["type"]) == {"string", "null"}
            first = await mcp_call(session, "session_start", {"brain_session": "new"})
            assert "result_session" in first, error_summary(first)
            credential = first["result_session"]["credential"]
            complete, payload, frames = await mcp_window(session, first, credential)
            assert len(frames) > 1
            assert_complete_text(frames, complete)
            lines = await mcp_call(session, "result_read", {
                "brain_session": credential, "result_id": first["delivery"]["result_id"],
                "page": 0, "page_unit": "lines", "page_size": 3})
            assert lines["status"] == "ok", error_summary(lines)
            assert lines["delivery"]["unit"] == "lines"
            assert lines["result"]["content"] == "".join(complete.splitlines(keepends=True)[:3])
            assert isinstance(lines["delivery"]["continuation"], str)
            following = await mcp_call(session, "result_read", {
                "brain_session": credential, "result_id": first["delivery"]["result_id"],
                "continuation": lines["delivery"]["continuation"]})
            assert following["delivery"]["unit"] == "lines"
            assert following["delivery"]["page_size"] == 3
            assert following["delivery"]["page"] == 2
            wrong = await mcp_call(session, "result_read", {
                "brain_session": credential, "result_id": first["delivery"]["result_id"],
                "page": 0, "page_unit": "records"})
            assert wrong["status"] == "error" and wrong["error"]["code"] == "conflict"
            assert "bytes, lines" in wrong["error"]["message"]
            assert complete == (root / ".brain/local/session.md").read_text(encoding="utf-8")
            assert preferences in complete and gotchas in complete
            for mandatory in ("## Principles", "## Always Rules", "## Triggers", "## Authorisation"):
                assert mandatory in complete
            stored = root / ".brain/local/results"
            assert all(credential.encode() not in path.read_bytes() for path in stored.rglob("*") if path.is_file())

    anyio.run(exercise)


def test_mcp_sessions_isolate_cached_reads_and_write_output_never_replays(delivery_vault):
    root, env, _, _ = delivery_vault
    path = root / TARGET
    original = path.read_text(encoding="utf-8") + "\n" + "".join(
        f"Read line {i:04}: colour 雪, escaped \"text\" and \\.\n" for i in range(650)) + ("雪😀\"\\" * 1500)
    path.write_text(original, encoding="utf-8")

    async def exercise():
        async with mcp_client(env) as session:
            opened = [await mcp_call(session, "session_start", {"brain_session": "new"}) for _ in range(2)]
            assert all("result_session" in frame for frame in opened), [error_summary(frame) for frame in opened]
            one, two = [frame["result_session"]["credential"] for frame in opened]
            assert one != two
            first = await mcp_call(session, "artefact_read", {"brain_session": one, "reference": TARGET})
            result_id = first["delivery"]["result_id"]
            denied = await mcp_call(session, "result_read", {"brain_session": two, "result_id": result_id})
            assert denied["status"] == "error" and denied["result"] is None
            assert original[:100] not in json.dumps(denied)
            other = await mcp_call(session, "artefact_read", {"brain_session": two, "reference": TARGET})
            assert other["status"] == "ok"
            assert other["delivery"]["result_id"] != result_id
            complete, payload, frames = await mcp_window(session, first, one)
            assert_complete_text(frames, original)
            assert len(frames) > 1
            # This result's source bytes now differ: cache retrieval must still
            # return the complete immutable output without executing artefact.read.
            changed = original.replace("Read line", "Changed line")
            path.write_text(changed, encoding="utf-8")
            cached = await mcp_call(session, "result_read", {
                "brain_session": one, "result_id": result_id, "page": 0,
            })
            assert cached["status"] == "ok", error_summary(cached)
            cached_chunk, _, cached_frames = await mcp_window(session, cached, one)
            assert_complete_text(cached_frames, original)
            assert cached_chunk == complete
            assert "Changed line" not in cached_chunk
            # Both agents used the same canonical read command and principal;
            # the second session's original result still has its own bytes.
            other_page = await mcp_call(session, "result_read", {
                "brain_session": two, "result_id": other["delivery"]["result_id"], "page": 1,
            })
            assert other_page["status"] == "ok", error_summary(other_page)
            assert text_content(other_page["result"]) == text_content(other["result"])
            marker = "\nOnce-only append from the stdio delivery test.\n"
            write = await mcp_call(session, "document_write-body", {
                "brain_session": one, "document": {"resource": "artefact", "reference": TARGET},
                "expected_revision": document_revision_at(path), "operation": "append",
                "content": {"source": "inline", "content": marker},
            })
            assert write["status"] == "ok"
            after = path.read_bytes()
            for _ in range(2):
                page = await mcp_call(session, "result_read", {
                    "brain_session": one, "result_id": write["delivery"]["result_id"], "page": 0,
                })
                assert page["status"] == "ok"
                assert page["command"] == "document.write-body"
                assert path.read_bytes() == after
            assert after.decode().count(marker) == 1
            # Another originating command did not replace the read slot.
            cached = await mcp_call(session, "result_read", {"brain_session": one, "result_id": result_id, "page": 1})
            assert cached["status"] == "ok"
            assert text_content(cached["result"]) == text_content(first["result"])

    anyio.run(exercise)


def test_cli_private_file_resumes_in_new_process_page_zero_next_and_clear(delivery_vault, tmp_path):
    root, env, _, _ = delivery_vault
    session_file = tmp_path / "private-result-session"
    command = root / ".brain-core/scripts/command.py"

    def call(noun, verb, payload=None, *, bootstrap=False, expected_code=0):
        entry = [str(SERVER), "--cli-bootstrap"] if bootstrap else [str(command)]
        completed = subprocess.run(
            [sys.executable, *entry, noun, verb, "--vault", str(root),
             "--result-session-file", str(session_file), "--json", "--request-json", "-"],
            input=json.dumps(payload or {}), cwd=tmp_path, env=env,
            text=True, capture_output=True, timeout=15,
        )
        assert completed.returncode == expected_code, (
            completed.stderr[-1000:], error_summary(json.loads(completed.stdout)) if completed.stdout else None)
        frame = json.loads(completed.stdout)
        assert len(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode()) <= 8000
        assert len(completed.stdout.encode("utf-8")) <= 32768
        if session_file.exists():
            secret = session_file.read_text()
            assert secret not in completed.stdout + completed.stderr
        return frame

    issued = call("session", "start", bootstrap=True)
    assert issued["status"] == "ok"
    assert Path(issued["result_session_file"]) == session_file
    assert session_file.stat().st_mode & 0o777 == 0o600
    credential = session_file.read_bytes()
    description = call("command", "describe", {"target_command_id": "result.read"})
    schema = json.loads(description["result"]["request_schema_json"])
    continuation_schema = schema["properties"]["continuation"]
    assert set(continuation_schema["type"]) == {"string", "null"}

    source = root / TARGET
    source.write_text(source.read_text() + "\n" + "".join(
        f"CLI line {i:04}: colour 雪 and escaped \"text\".\n" for i in range(350)) + ("雪😀\"\\" * 1500), encoding="utf-8")
    first = call("artefact", "read", {"reference": TARGET})
    result_id = first["delivery"]["result_id"]
    page_zero = call("result", "read", {"result_id": result_id, "page": 0})
    assert page_zero["status"] == "ok"
    assert page_zero["delivery"]["page"] == 1
    assert page_zero["delivery"]["continuation"]
    assert page_zero["result"] == first["result"]
    pages = [page_zero]
    for _ in range(30):
        cursor = pages[-1]["delivery"]["continuation"]
        if cursor is None:
            break
        pages.append(call("result", "read", {"result_id": result_id, "continuation": cursor}))
        assert pages[-1]["status"] == "ok"
    else:
        pytest.fail("CLI delivery did not finish within the test work bound")
    assert_complete_text(pages, source.read_text(encoding="utf-8"))
    assert len(pages) > 1
    lines = call("result", "read", {"result_id": result_id, "page": 0,
        "page_unit": "lines", "page_size": 3})
    assert lines["delivery"]["unit"] == "lines"
    assert lines["result"]["content"] == "".join(source.read_text(encoding="utf-8").splitlines(keepends=True)[:3])
    byte_view = call("result", "read", {"result_id": result_id,
        "continuation": lines["delivery"]["continuation"], "page_unit": "bytes",
        "page_size": 120, "anchor": lines["result"]["content_range"]["end"]})
    assert byte_view["delivery"]["unit"] == "bytes"
    assert len(byte_view["result"]["content"].encode("utf-8")) <= 120
    assert session_file.read_bytes() == credential
    source_bytes = source.read_bytes()
    cleared = call("result", "clear")
    assert cleared["status"] == "ok" and cleared["result"]["cleared"]
    missing = call("result", "read", {"result_id": result_id}, expected_code=3)
    assert missing["status"] == "error" and missing["result"] is None
    assert missing["error"]["code"] == "capability_unavailable"
    assert missing["error"]["effects"] == "none"
    assert missing["error"]["retryable"] is False
    assert missing["error"].get("next_action") is None
    assert missing["delivery"]["status"] == missing["delivery"]["availability"] == "unavailable"
    assert missing["delivery"]["reason"] == "result_unavailable"
    assert missing["delivery"]["result_id"] == result_id
    assert missing["delivery"].get("total_items") is missing["delivery"].get("total_pages") is None
    assert session_file.read_bytes() == credential
    assert source.read_bytes() == source_bytes
    reused = call("artefact", "read", {"reference": TARGET})
    assert reused["status"] == "ok" and reused["delivery"]["result_id"] != result_id
    malformed = call("result", "read", {"result_id": reused["delivery"]["result_id"],
        "continuation": "t1_malformed"}, expected_code=2)
    assert malformed["error"]["code"] == "conflict" and malformed["error"]["effects"] == "none"
    assert session_file.read_bytes() == credential
    assert source.read_bytes() == source_bytes
    invalid = call("result", "read", {"result_id": reused["delivery"]["result_id"],
        "continuation": {"page": 2}}, expected_code=2)
    assert invalid["error"]["code"] == "invalid_request"
    assert "opaque token" in invalid["error"]["message"]


def test_mcp_clear_preserves_other_sessions_source_and_selected_credential(delivery_vault):
    root, env, _, _ = delivery_vault
    source_bytes = (root / TARGET).read_bytes()

    async def exercise():
        async with mcp_client(env) as session:
            first = await mcp_call(session, 'session_start', {'brain_session': 'new'})
            second = await mcp_call(session, 'session_start', {'brain_session': 'new'})
            own = first['result_session']['credential']
            other = second['result_session']['credential']
            own_read = await mcp_call(session, 'artefact_read', {'brain_session': own, 'reference': TARGET})
            other_read = await mcp_call(session, 'artefact_read', {'brain_session': other, 'reference': TARGET})
            cleared = await mcp_call(session, 'result_clear', {'brain_session': own})
            assert cleared['status'] == 'ok'
            assert cleared['result'] == {'cleared': True}
            assert 'result_session' not in cleared
            missing = await mcp_call(session, 'result_read', {
                'brain_session': own, 'result_id': own_read['delivery']['result_id']})
            assert missing['status'] == 'error' and missing['result'] is None
            preserved = await mcp_call(session, 'result_read', {
                'brain_session': other, 'result_id': other_read['delivery']['result_id']})
            assert preserved['status'] == 'ok'
            reused = await mcp_call(session, 'artefact_read', {'brain_session': own, 'reference': TARGET})
            assert reused['status'] == 'ok'
            assert reused['delivery']['result_id'] != own_read['delivery']['result_id']
            assert 'result_session' not in reused

    anyio.run(exercise)
    assert (root / TARGET).read_bytes() == source_bytes


def test_cli_cp1252_json_preserves_unicode_write_effects_and_retained_outcome(delivery_vault, tmp_path):
    root, env, _, _ = delivery_vault
    env = {**env, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
    session = tmp_path / "cp1252-session"
    command = root / ".brain-core/scripts/command.py"

    def call(noun, verb, payload, *, bootstrap=False):
        entry = [str(SERVER), "--cli-bootstrap"] if bootstrap else [str(command)]
        completed = subprocess.run(
            [sys.executable, *entry, noun, verb, "--vault", str(root),
             "--result-session-file", str(session), "--json", "--request-json", "-"],
            input=json.dumps(payload).encode("ascii"), cwd=tmp_path, env=env,
            capture_output=True, timeout=30,
        )
        assert completed.returncode == 0, completed.stderr.decode("cp1252")
        assert completed.stdout.isascii()
        return json.loads(completed.stdout.decode("cp1252"))

    call("session", "start", {}, bootstrap=True)
    path = root / TARGET
    before = path.read_bytes()
    marker = "\nOnce-only CP1252 boundary: 雪 😀.\n"
    written = call("document", "write-body", {
        "document": {"resource": "artefact", "reference": TARGET},
        "expected_revision": document_revision_at(path), "operation": "append",
        "content": {"source": "inline", "content": marker},
    })
    assert written["status"] == "ok"
    from _common import parse_frontmatter
    assert parse_frontmatter(path.read_text(encoding="utf-8"))[1] == parse_frontmatter(before.decode("utf-8"))[1] + marker
    assert written.get("committed_effects")
    after = path.read_bytes()
    replay = call("result", "read", {"result_id": written["delivery"]["result_id"], "page": 0})
    assert replay["status"] == "ok"
    assert replay.get("committed_effects") == written["committed_effects"]
    assert path.read_bytes() == after
