"""Tests for build_lexical_index.py — portable lexical index builder."""

import json

from _search.paths import OUTPUT_PATH


def write_vault(vault):
    """Populate a minimal vault for lexical index builds."""
    bc = vault / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (vault / "_Config").mkdir()

    wiki = vault / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language.\n"
    )
    (wiki / "rust-ownership.md").write_text(
        "---\ntype: living/wiki\ntags: [rust]\nstatus: active\n---\n\n"
        "# Rust Ownership\n\nRust uses an ownership system.\n"
    )


def test_main_builds_and_persists_index(tmp_path, wrapper_cli):
    write_vault(tmp_path)

    result = wrapper_cli(tmp_path, "build_lexical_index.py")

    assert result.returncode == 0
    assert (tmp_path / OUTPUT_PATH).exists()
    assert "Built lexical retrieval index:" in result.stderr


def test_main_json_mode_prints_index_without_persisting(tmp_path, wrapper_cli):
    write_vault(tmp_path)

    result = wrapper_cli(tmp_path, "build_lexical_index.py", "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["meta"]["document_count"] == 2
    assert not (tmp_path / OUTPUT_PATH).exists()


def test_main_reports_unreadable_retrieval_sources(tmp_path, wrapper_cli):
    write_vault(tmp_path)
    (tmp_path / "Wiki" / "broken.md").write_bytes(b"\xff\xfe\x00\x00")

    result = wrapper_cli(tmp_path, "build_lexical_index.py")

    assert result.returncode == 1
    assert "unreadable retrieval source 'Wiki/broken.md'" in result.stderr
    assert "while building lexical retrieval state" in result.stderr
