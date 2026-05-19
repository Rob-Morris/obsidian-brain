"""Tests for search_lexical.py — portable lexical retrieval search."""

import json

import _search.index as search_index_mod


def build_and_persist_index(vault):
    """Build and persist the retrieval index for CLI wrapper tests."""
    index = search_index_mod.build_index(vault).index
    search_index_mod.persist_retrieval_index(vault, index)
    return index


def write_vault(vault):
    """Populate a minimal searchable vault."""
    bc = vault / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (vault / "_Config").mkdir()

    wiki = vault / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python, programming]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language.\n"
    )
    (wiki / "rust-ownership.md").write_text(
        "---\ntype: living/wiki\ntags: [rust, systems]\nstatus: active\n---\n\n"
        "# Rust Ownership\n\nRust uses an ownership system.\n"
    )

    temporal = vault / "_Temporal" / "Logs" / "2026-03"
    temporal.mkdir(parents=True)
    (temporal / "20260315-python-log.md").write_text(
        "---\ntype: temporal/logs\ntags: [python, log]\nstatus: done\n---\n\n"
        "# Python Research Log\n\nResearched Python packaging tools.\n"
    )


def test_main_returns_json_results_for_query(tmp_path, wrapper_cli):
    write_vault(tmp_path)
    build_and_persist_index(tmp_path)

    result = wrapper_cli(tmp_path, "search_lexical.py", "python", "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload
    assert payload[0]["path"].endswith("python-basics.md")


def test_main_errors_without_query(tmp_path, wrapper_cli):
    write_vault(tmp_path)
    build_and_persist_index(tmp_path)

    result = wrapper_cli(tmp_path, "search_lexical.py")

    assert result.returncode == 1
    assert "Usage: search_lexical.py" in result.stderr


def test_main_respects_filters_and_top_k(tmp_path, wrapper_cli):
    write_vault(tmp_path)
    build_and_persist_index(tmp_path)

    result = wrapper_cli(
        tmp_path,
        "search_lexical.py",
        "python",
        "--type",
        "temporal/logs",
        "--tag",
        "log",
        "--status",
        "done",
        "--top-k",
        "1",
        "--json",
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["path"] == "_Temporal/Logs/2026-03/20260315-python-log.md"


def test_main_rejects_mode_flag(tmp_path, wrapper_cli):
    write_vault(tmp_path)
    build_and_persist_index(tmp_path)

    result = wrapper_cli(tmp_path, "search_lexical.py", "python", "--mode", "semantic")

    assert result.returncode == 2
    assert "unrecognized arguments: --mode semantic" in result.stderr
