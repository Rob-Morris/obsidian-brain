"""Undecodable composed inputs retain diagnostic paths without codec failures."""

from pathlib import Path
import json

import pytest

from _bootstrap import diagnostics, mcp_state
from _lifecycle.derived_cache_state import (
    RouterCacheUnavailable,
    inspect_lexical_cache,
    inspect_router_cache,
    require_fresh_compiled_router,
)
import check


BAD_TEXT = b"user-owned text\xff\n"
ROUTER = ".brain/local/compiled-router.json"
LEXICAL = ".brain/local/retrieval-index.json"
CLIENT_FILES = (
    ".mcp.json",
    ".codex/config.toml",
    ".claude/settings.local.json",
    "CLAUDE.md",
    mcp_state.INIT_STATE_REL,
)


def _write(root: Path, relative: str, content: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize("relative,inspect", [(ROUTER, inspect_router_cache), (LEXICAL, inspect_lexical_cache)])
@pytest.mark.parametrize("content,reason", [(BAD_TEXT, "unreadable"), (b"{broken", "invalid-json")])
def test_cache_readers_preserve_distinct_failure_reasons(tmp_path, relative, inspect, content, reason):
    path = _write(tmp_path, relative, content)
    state = inspect(tmp_path)
    assert state.stale and state.reason == reason
    assert state.path == relative and state.payload is None
    assert path.read_bytes() == content


def test_router_admission_retains_unreadable_reason(tmp_path):
    from _application.results import router_cache_error

    _write(tmp_path, ROUTER, BAD_TEXT)
    with pytest.raises(RouterCacheUnavailable) as caught:
        require_fresh_compiled_router(tmp_path)
    assert caught.value.state.reason == "unreadable"
    assert "(unreadable)" in str(caught.value)
    error = router_cache_error(caught.value.state)
    assert error.details.reason == "unreadable"
    assert error.details.cache == ROUTER
    assert error.next_action.command_id == "runtime.refresh-router"


@pytest.mark.parametrize("content", [BAD_TEXT, b"{broken"])
def test_json_diagnostics_handle_decode_like_parse_failure(tmp_path, content):
    path = _write(tmp_path, ".mcp.json", content)
    payload, error = diagnostics._read_json_safe(path)
    assert payload is None and error
    assert path.read_bytes() == content


def test_toml_diagnostics_handle_decode_like_read_failure(tmp_path):
    path = _write(tmp_path, ".codex/config.toml", BAD_TEXT)
    assert mcp_state.read_toml_server_config(path) is None
    assert path.read_bytes() == BAD_TEXT


@pytest.mark.parametrize("relative", CLIENT_FILES)
def test_real_mcp_inspection_survives_undecodable_inputs(command_vault_clone, fake_home, relative):
    tmp_path = command_vault_clone.vault_root
    path = _write(tmp_path, relative, BAD_TEXT)
    state = diagnostics.inspect_mcp(tmp_path)
    assert set(state) == {"server_config", "claude", "codex", "grok"}
    assert path.read_bytes() == BAD_TEXT
    if relative == "CLAUDE.md":
        assert not state["claude"]["healthy"]
        assert state["claude"]["bootstrap_reason"] == "unreadable"
        assert "vault.check" in state["claude"]["bootstrap_message"]


@pytest.mark.parametrize("content,reason", [(BAD_TEXT, "unreadable"), (b"No bootstrap line\n", None)])
def test_claude_unreadable_is_distinct_from_missing_bootstrap(command_vault_clone, fake_home, content, reason):
    tmp_path = command_vault_clone.vault_root
    server = diagnostics._expected_project_server_config(tmp_path)
    _write(tmp_path, ".mcp.json", json.dumps({"mcpServers": {"brain": server}}).encode())
    hook = diagnostics.build_session_hook_command(tmp_path, tmp_path, python_path=server["command"])
    _write(tmp_path, ".claude/settings.local.json", json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": hook}]}]}}).encode())
    _write(tmp_path, "CLAUDE.md", content)
    state = diagnostics.inspect_mcp(tmp_path)
    assert not state["claude"]["bootstrap_ok"]
    assert state["claude"]["bootstrap_reason"] == reason
    findings = diagnostics.collect_mcp_check_findings(tmp_path)
    registration = next(f for f in findings if f["check"] == "mcp_registration")
    if reason:
        assert "CLAUDE.md is unreadable" in registration["message"]
        assert "vault.check" in registration["message"]
        assert "missing" not in registration["message"]
    else:
        assert "drifted or incomplete" in registration["message"]


@pytest.mark.parametrize("router_state", ["present", "missing", "unreadable"])
@pytest.mark.parametrize("local_mcp", [False, True])
def test_run_checks_survives_undecodable_composed_files(command_vault_clone, fake_home, router_state, local_mcp):
    root = command_vault_clone.vault_root
    # Bootstrap-only files do not activate MCP inspection; transport files do.
    relatives = [LEXICAL, "CLAUDE.md", ".claude/settings.local.json"]
    if local_mcp:
        relatives += [".mcp.json", ".codex/config.toml", mcp_state.INIT_STATE_REL]
    for relative in relatives:
        _write(root, relative, BAD_TEXT)
    router = root / ROUTER
    if router_state == "missing":
        router.unlink()
    elif router_state == "unreadable":
        router.write_bytes(BAD_TEXT)
    assert diagnostics.local_mcp_state_present(root) is local_mcp
    result = check.run_checks(str(root))
    assert isinstance(result["findings"], list)
    serialised = json.dumps(result)
    assert "codec can't decode" not in serialised
    if router_state == "present":
        lexical = next(f for f in result["findings"] if f["check"] == "lexical_index")
        assert "(unreadable)" in lexical["message"]
    else:
        finding = next(f for f in result["findings"] if f["check"] == "router")
        assert ("not found" if router_state == "missing" else "(unreadable)") in finding["message"]
    for relative in relatives:
        assert (root / relative).read_bytes() == BAD_TEXT
