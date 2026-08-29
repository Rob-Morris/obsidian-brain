"""Regression tests for terminal-status living-key compatibility."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import migrate_to_0_62_4
import upgrade
from check import check_living_key_fields


ARTEFACT = {
    "classification": "living",
    "frontmatter_type": "living/design",
    "key": "designs",
    "path": "Designs",
}
ROUTER = {"artefacts": [ARTEFACT]}


def _write(path: Path, *, title: str, key: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: living/design",
        "tags:",
        "  - design/brain",
    ]
    if key is not None:
        lines.append(f"key: {key}")
    lines.extend([f"title: {title}", "---", "", f"# {title}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _compiled_router(vault: Path) -> None:
    local = vault / ".brain" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "compiled-router.json").write_text(
        json.dumps(ROUTER), encoding="utf-8"
    )


def test_backfills_only_keyless_living_records_in_status_folders(tmp_path):
    _write(tmp_path / "Designs" / "Inherited.md", title="Inherited")
    _write(
        tmp_path / "Designs" / "+Deprecated" / "Legacy.md",
        title="Legacy Design",
    )

    result = migrate_to_0_62_4.plan(str(tmp_path), router=ROUTER)
    assert [(item["path"], item["key"]) for item in result["updates"]] == [
        ("Designs/+Deprecated/Legacy.md", "legacy-design")
    ]

    _compiled_router(tmp_path)
    applied = migrate_to_0_62_4.migrate(str(tmp_path))

    assert applied["status"] == "ok"
    assert "key: legacy-design" in (
        tmp_path / "Designs" / "+Deprecated" / "Legacy.md"
    ).read_text(encoding="utf-8")
    assert "key:" not in (tmp_path / "Designs" / "Inherited.md").read_text(
        encoding="utf-8"
    )
    assert migrate_to_0_62_4.migrate(str(tmp_path)) == {
        "status": "skipped",
        "updated": [],
    }


def test_accounts_for_existing_keys_across_the_complete_type(tmp_path):
    _write(
        tmp_path / "Designs" / "Current.md",
        title="Current",
        key="legacy-design",
    )
    _write(
        tmp_path / "Designs" / "+Deprecated" / "Legacy.md",
        title="Legacy Design",
    )

    result = migrate_to_0_62_4.plan(str(tmp_path), router=ROUTER)

    update = result["updates"][0]
    assert update["path"] == "Designs/+Deprecated/Legacy.md"
    assert update["key"] != "legacy-design"


def test_read_failure_blocks_every_write(tmp_path, monkeypatch):
    first = tmp_path / "Designs" / "+Deprecated" / "First.md"
    second = tmp_path / "Designs" / "+Deprecated" / "Second.md"
    _write(first, title="First")
    _write(second, title="Second")
    original = migrate_to_0_62_4.read_artefact

    def fail_one(path):
        if Path(path) == second:
            raise OSError("unreadable")
        return original(path)

    monkeypatch.setattr(migrate_to_0_62_4, "read_artefact", fail_one)
    _compiled_router(tmp_path)

    result = migrate_to_0_62_4.migrate(str(tmp_path))

    assert result["status"] == "error"
    assert result["read_errors"] == [
        {"path": "Designs/+Deprecated/Second.md", "error": "unreadable"}
    ]
    assert "key:" not in first.read_text(encoding="utf-8")


def test_v054_ledger_runs_new_backfill_without_repairing_inherited_errors(tmp_path):
    vault = tmp_path / "Brain"
    _write(vault / "Designs" / "Inherited.md", title="Inherited")
    _write(
        vault / "Designs" / "+Deprecated" / "Legacy.md",
        title="Legacy Design",
    )
    _compiled_router(vault)
    migrations = vault / ".brain-core" / "scripts" / "migrations"
    migrations.mkdir(parents=True)
    shutil.copy2(migrate_to_0_62_4.__file__, migrations / "migrate_to_0_62_4.py")
    ledger = vault / ".brain" / "local" / "migrations.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "migrations": {
                    "0.31.0": {
                        "status": "ok",
                        "recorded_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    before = check_living_key_fields(str(vault), ROUTER)

    results, recorded = upgrade._run_migrations(
        str(vault), "0.54.0", "0.62.4", raise_on_error=True
    )
    after = check_living_key_fields(str(vault), ROUTER)

    assert len(before) == 2
    assert [finding["file"] for finding in after] == ["Designs/Inherited.md"]
    assert results[0]["version"] == "0.62.4"
    assert results[0]["status"] == "ok"
    assert recorded["migrations"]["0.62.4"]["status"] == "ok"
