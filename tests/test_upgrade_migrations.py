"""Tests for upgrade.py migration ledger and force semantics."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import upgrade


_REAL_SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"


def _make_source(tmp_path: Path, version: str, *, migrations: dict[str, str]) -> Path:
    source = tmp_path / f"source-{version.replace('.', '-')}"
    source.mkdir()
    (source / "VERSION").write_text(version + "\n")
    (source / "session-core.md").write_text("# Session Core\n")
    (source / "index.md").write_text("# Index\n")
    (source / "md-bootstrap.md").write_text("# Markdown Bootstrap\n")

    shutil.copytree(_REAL_SCRIPTS, source / "scripts")
    upgrade_in_source = source / "scripts" / "upgrade.py"
    if upgrade_in_source.exists():
        upgrade_in_source.unlink()
    (source / "scripts" / "compile_router.py").write_text("import sys; sys.exit(0)\n")

    migrations_dir = source / "scripts" / "migrations"
    for name in list(migrations_dir.glob("migrate_to_*.py")):
        name.unlink()
    for name, body in migrations.items():
        (migrations_dir / name).write_text(body)

    return source


def _make_vault(tmp_path: Path, version: str) -> Path:
    vault = tmp_path / f"vault-{version.replace('.', '-')}"
    vault.mkdir()
    brain_core = vault / ".brain-core"
    brain_core.mkdir()
    (brain_core / "VERSION").write_text(version + "\n")
    (brain_core / "session-core.md").write_text("# Session Core\n")
    scripts = brain_core / "scripts"
    scripts.mkdir()
    (scripts / "compile_router.py").write_text("import sys; sys.exit(0)\n")

    (vault / "_Config").mkdir()
    (vault / "_Config" / "router.md").write_text("Brain vault.\n")

    (vault / ".brain").mkdir()
    (vault / ".brain" / "local").mkdir()
    (vault / ".brain" / "preferences.json").write_text("{}\n")
    (vault / ".brain" / "tracking.json").write_text("{}\n")
    return vault


def _ledger(vault: Path) -> dict:
    return json.loads((vault / ".brain" / "local" / "migrations.json").read_text())


def _counter(vault: Path, name: str) -> int:
    return int((vault / ".brain" / "local" / name).read_text().strip())


def test_run_pending_migrations_records_ledger_and_skips_repeat(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={
            "migrate_to_1_0_0.py": (
                "import os\n"
                "\n"
                "def migrate(vault_root):\n"
                "    path = os.path.join(vault_root, '.brain', 'local', 'count-1.txt')\n"
                "    count = 0\n"
                "    if os.path.exists(path):\n"
                "        with open(path, 'r', encoding='utf-8') as f:\n"
                "            count = int(f.read().strip())\n"
                "    with open(path, 'w', encoding='utf-8') as f:\n"
                "        f.write(str(count + 1))\n"
                "    return {'status': 'ok', 'actions': ['counted']}\n"
            ),
        },
    )
    vault = _make_vault(tmp_path, "1.0.0")
    shutil.copytree(source / "scripts" / "migrations", vault / ".brain-core" / "scripts" / "migrations")

    first = upgrade.run_pending_migrations(str(vault))
    second = upgrade.run_pending_migrations(str(vault))

    assert len(first) == 1
    assert second == []
    assert _counter(vault, "count-1.txt") == 1
    assert _ledger(vault)["migrations"]["1.0.0"]["status"] == "ok"
    assert (vault / ".brain" / "local" / ".migrated-version").read_text().strip() == "1.0.0"


def test_run_pending_migrations_force_reruns_recorded_migration(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={
            "migrate_to_1_0_0.py": (
                "import os\n"
                "\n"
                "def migrate(vault_root):\n"
                "    path = os.path.join(vault_root, '.brain', 'local', 'count-force.txt')\n"
                "    count = 0\n"
                "    if os.path.exists(path):\n"
                "        with open(path, 'r', encoding='utf-8') as f:\n"
                "            count = int(f.read().strip())\n"
                "    with open(path, 'w', encoding='utf-8') as f:\n"
                "        f.write(str(count + 1))\n"
                "    return {'status': 'ok'}\n"
            ),
        },
    )
    vault = _make_vault(tmp_path, "1.0.0")
    shutil.copytree(source / "scripts" / "migrations", vault / ".brain-core" / "scripts" / "migrations")

    upgrade.run_pending_migrations(str(vault))
    forced = upgrade.run_pending_migrations(str(vault), force=True)

    assert len(forced) == 1
    assert _counter(vault, "count-force.txt") == 2
    assert _ledger(vault)["migrations"]["1.0.0"]["status"] == "ok"


def test_upgrade_backfills_old_versions_and_prevents_startup_rerun(tmp_path):
    source = _make_source(
        tmp_path,
        "2.0.0",
        migrations={
            "migrate_to_1_0_0.py": (
                "import os\n"
                "\n"
                "def migrate(vault_root):\n"
                "    path = os.path.join(vault_root, '.brain', 'local', 'count-old.txt')\n"
                "    count = 0\n"
                "    if os.path.exists(path):\n"
                "        with open(path, 'r', encoding='utf-8') as f:\n"
                "            count = int(f.read().strip())\n"
                "    with open(path, 'w', encoding='utf-8') as f:\n"
                "        f.write(str(count + 1))\n"
                "    return {'status': 'ok'}\n"
            ),
            "migrate_to_2_0_0.py": (
                "import os\n"
                "\n"
                "def migrate(vault_root):\n"
                "    path = os.path.join(vault_root, '.brain', 'local', 'count-new.txt')\n"
                "    count = 0\n"
                "    if os.path.exists(path):\n"
                "        with open(path, 'r', encoding='utf-8') as f:\n"
                "            count = int(f.read().strip())\n"
                "    with open(path, 'w', encoding='utf-8') as f:\n"
                "        f.write(str(count + 1))\n"
                "    return {'status': 'ok'}\n"
            ),
        },
    )
    vault = _make_vault(tmp_path, "1.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)
    startup = upgrade.run_pending_migrations(str(vault))
    ledger = _ledger(vault)

    assert result["status"] == "ok"
    assert [item["version"] for item in result["migrations"]] == ["2.0.0"]
    assert startup == []
    assert ledger["migrations"]["1.0.0"]["status"] == "backfilled"
    assert ledger["migrations"]["2.0.0"]["status"] == "ok"
    assert _counter(vault, "count-new.txt") == 1
    assert not (vault / ".brain" / "local" / "count-old.txt").exists()
    assert (vault / ".brain" / "local" / ".migrated-version").read_text().strip() == "2.0.0"
