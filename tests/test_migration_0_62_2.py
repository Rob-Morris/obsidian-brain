"""Tests for the v0.62.2 shaping-transcript router migration."""

import json
from pathlib import Path
import shutil

import pytest

from migrate_to_0_62_2 import NEW_RULE, OLD_RULE, patch_pre_compile
import upgrade


def _router(vault: Path, rule: str) -> Path:
    path = vault / "_Config" / "router.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"Conditional:\n{rule}\n", encoding="utf-8")
    return path


def test_replaces_only_the_previous_shipped_rule(tmp_path):
    router = _router(tmp_path, OLD_RULE)

    result = patch_pre_compile(str(tmp_path), context={})

    assert result == {"status": "ok", "updated": ["_Config/router.md"]}
    assert router.read_text(encoding="utf-8") == f"Conditional:\n{NEW_RULE}\n"


def test_preserves_a_custom_shaping_transcript_rule(tmp_path):
    custom = (
        "- After my custom shaping flow → "
        "[[_Config/Taxonomy/Temporal/shaping-transcripts]]"
    )
    router = _router(tmp_path, custom)

    result = patch_pre_compile(str(tmp_path), context={})

    assert result == {"status": "skipped", "updated": []}
    assert router.read_text(encoding="utf-8") == f"Conditional:\n{custom}\n"


@pytest.mark.parametrize(
    "custom_rule",
    (
        OLD_RULE + " <!-- keep my annotation -->",
        "  " + OLD_RULE,
        "Prefix " + OLD_RULE,
    ),
)
def test_preserves_non_exact_variants_of_the_old_rule(tmp_path, custom_rule):
    router = _router(tmp_path, custom_rule)

    result = patch_pre_compile(str(tmp_path), context={})

    assert result == {"status": "skipped", "updated": []}
    assert router.read_text(encoding="utf-8") == f"Conditional:\n{custom_rule}\n"


def test_preserves_line_endings_when_replacing_the_exact_rule(tmp_path):
    router = _router(tmp_path, OLD_RULE)
    router.write_bytes(f"Conditional:\r\n{OLD_RULE}\r\n".encode())

    result = patch_pre_compile(str(tmp_path), context={})

    assert result["status"] == "ok"
    assert router.read_bytes() == f"Conditional:\r\n{NEW_RULE}\r\n".encode()


def test_upgrade_compiles_the_confirmed_plan_trigger_before_sync(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "VERSION").write_text("0.62.2\n", encoding="utf-8")
    (source / "session-core.md").write_text(
        "Always:\n- Keep the vault coherent.\n", encoding="utf-8"
    )
    (source / "index.md").write_text("# Index\n", encoding="utf-8")
    (source / "md-bootstrap.md").write_text(
        "# Markdown Bootstrap\n", encoding="utf-8"
    )
    shutil.copytree("src/brain-core/scripts", source / "scripts")
    (source / "scripts" / "upgrade.py").unlink()

    vault = tmp_path / "Brain"
    router = _router(vault, OLD_RULE)
    installed = vault / ".brain-core"
    installed.mkdir()
    (installed / "VERSION").write_text("0.62.1\n", encoding="utf-8")
    (installed / "session-core.md").write_text(
        "Always:\n- Keep the vault coherent.\n", encoding="utf-8"
    )
    scripts = installed / "scripts"
    scripts.mkdir()
    (scripts / "compile_router.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    brain = vault / ".brain"
    brain.mkdir()
    (brain / "preferences.json").write_text(
        '{"artefact_sync": "skip"}\n', encoding="utf-8"
    )
    (brain / "local").mkdir()
    (vault / "_Temporal" / "Shaping Transcripts").mkdir(parents=True)
    taxonomy = vault / "_Config" / "Taxonomy" / "Temporal"
    taxonomy.mkdir(parents=True)
    source_taxonomy = Path(
        "src/brain-core/artefact-library/temporal/shaping-transcripts/taxonomy.md"
    )
    (taxonomy / "shaping-transcripts.md").write_text(
        source_taxonomy.read_text(encoding="utf-8"), encoding="utf-8"
    )

    result = upgrade.upgrade(
        str(vault),
        str(source),
        sync=False,
        sync_deps=False,
    )
    compiled = json.loads(
        (vault / ".brain" / "local" / "compiled-router.json").read_text(
            encoding="utf-8"
        )
    )
    trigger = next(
        item
        for item in compiled["triggers"]
        if item["target"] == "_Config/Taxonomy/Temporal/shaping-transcripts"
    )

    assert result["status"] == "ok"
    assert result["precompile_patch_migrations"] == [
        {
            "status": "ok",
            "updated": ["_Config/router.md"],
            "version": "0.62.2",
            "target": "pre_compile_patch",
        }
    ]
    assert "sync_result" not in result
    assert NEW_RULE in router.read_text(encoding="utf-8")
    assert trigger["condition"] == (
        "When a confirmed shaping plan selects a Brain transcript"
    )
    assert "only when that plan selects Brain" in trigger["detail"]
