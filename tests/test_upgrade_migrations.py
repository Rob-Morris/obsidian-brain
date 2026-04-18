"""Tests for upgrade.py migration ledger and force semantics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
import shutil

import pytest

import upgrade


_REAL_SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"


def _make_source(
    tmp_path: Path,
    version: str,
    *,
    migrations: dict[str, str] | None,
) -> Path:
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
    if migrations is not None:
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


def _counter_migration(filename: str) -> str:
    """Return migration source that increments a counter file in .brain/local/."""
    return (
        "import os\n"
        "\n"
        "def migrate(vault_root):\n"
        f"    path = os.path.join(vault_root, '.brain', 'local', '{filename}')\n"
        "    count = 0\n"
        "    if os.path.exists(path):\n"
        "        with open(path, 'r', encoding='utf-8') as f:\n"
        "            count = int(f.read().strip())\n"
        "    with open(path, 'w', encoding='utf-8') as f:\n"
        "        f.write(str(count + 1))\n"
        "    return {'status': 'ok'}\n"
    )


def _precompile_counter_migration(filename: str) -> str:
    """Return a target-specific patch migration that increments a counter."""
    return (
        "import os\n"
        "TARGET_HANDLERS = {'pre_compile_patch': 'patch_pre_compile'}\n"
        "\n"
        "def patch_pre_compile(vault_root, *, context=None):\n"
        f"    path = os.path.join(vault_root, '.brain', 'local', '{filename}')\n"
        "    count = 0\n"
        "    if os.path.exists(path):\n"
        "        with open(path, 'r', encoding='utf-8') as f:\n"
        "            count = int(f.read().strip())\n"
        "    with open(path, 'w', encoding='utf-8') as f:\n"
        "        f.write(str(count + 1))\n"
        "    return {'status': 'ok'}\n"
    )


def _ledger(vault: Path) -> dict:
    return json.loads((vault / ".brain" / "local" / "migrations.json").read_text())


def _counter(vault: Path, name: str) -> int:
    return int((vault / ".brain" / "local" / name).read_text().strip())


def test_run_pending_migrations_records_ledger_and_skips_repeat(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={"migrate_to_1_0_0.py": _counter_migration("count-1.txt")},
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
        migrations={"migrate_to_1_0_0.py": _counter_migration("count-force.txt")},
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
            "migrate_to_1_0_0.py": _counter_migration("count-old.txt"),
            "migrate_to_2_0_0.py": _counter_migration("count-new.txt"),
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


def test_target_specific_patch_migration_has_distinct_ledger_key(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={"migrate_to_1_0_0.py": _precompile_counter_migration("count-patch.txt")},
    )
    vault = _make_vault(tmp_path, "0.9.0")
    shutil.copytree(source / "scripts" / "migrations", vault / ".brain-core" / "scripts" / "migrations")

    first, ledger = upgrade._run_migrations(
        str(vault),
        "0.9.0",
        "1.0.0",
        target="pre_compile_patch",
        context={"compile_error": "stub"},
    )
    second, _ = upgrade._run_migrations(
        str(vault),
        "0.9.0",
        "1.0.0",
        target="pre_compile_patch",
        context={"compile_error": "stub"},
    )

    assert len(first) == 1
    assert second == []
    assert _counter(vault, "count-patch.txt") == 1
    assert ledger["migrations"]["1.0.0@pre_compile_patch"]["target"] == "pre_compile_patch"


def test_run_migrations_reimports_package_submodules_from_upgraded_scripts(tmp_path):
    stale_scripts = tmp_path / "stale-scripts"
    stale_pkg = stale_scripts / "migration_pkg"
    stale_pkg.mkdir(parents=True)
    (stale_pkg / "__init__.py").write_text("from ._value import VALUE\n")
    (stale_pkg / "_value.py").write_text("VALUE = 'old'\n")

    sys.path.insert(0, str(stale_scripts))
    try:
        import migration_pkg  # noqa: F401

        source = _make_source(
            tmp_path,
            "1.0.0",
            migrations={
                "migrate_to_1_0_0.py": (
                    "from migration_pkg import VALUE\n"
                    "import os\n"
                    "\n"
                    "def migrate(vault_root):\n"
                    "    path = os.path.join(vault_root, '.brain', 'local', 'package-value.txt')\n"
                    "    with open(path, 'w', encoding='utf-8') as f:\n"
                    "        f.write(VALUE)\n"
                    "    return {'status': 'ok'}\n"
                )
            },
        )
        fresh_pkg = source / "scripts" / "migration_pkg"
        fresh_pkg.mkdir(parents=True)
        (fresh_pkg / "__init__.py").write_text("from ._value import VALUE\n")
        (fresh_pkg / "_value.py").write_text("VALUE = 'new'\n")

        vault = _make_vault(tmp_path, "0.9.0")
        shutil.copytree(source / "scripts" / "migrations", vault / ".brain-core" / "scripts" / "migrations")
        shutil.copytree(fresh_pkg, vault / ".brain-core" / "scripts" / "migration_pkg")

        results, _ledger = upgrade._run_migrations(str(vault), "0.9.0", "1.0.0")

        assert len(results) == 1
        assert (vault / ".brain" / "local" / "package-value.txt").read_text() == "new"
    finally:
        sys.path.pop(0)
        for key in list(sys.modules):
            if key == "migration_pkg" or key.startswith("migration_pkg."):
                sys.modules.pop(key, None)


def test_target_handlers_requires_string_literal_function_names(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={
            "migrate_to_1_0_0.py": (
                "HANDLER_NAME = 'patch_pre_compile'\n"
                "TARGET_HANDLERS = {'pre_compile_patch': HANDLER_NAME}\n"
                "\n"
                "def patch_pre_compile(vault_root, *, context=None):\n"
                "    return {'status': 'ok'}\n"
            )
        },
    )
    vault = _make_vault(tmp_path, "0.9.0")
    shutil.copytree(source / "scripts" / "migrations", vault / ".brain-core" / "scripts" / "migrations")

    with pytest.raises(
        upgrade.MigrationDefinitionError,
        match=r"must map 'pre_compile_patch' to a string literal naming a top-level function",
    ):
        upgrade._run_migrations(
            str(vault),
            "0.9.0",
            "1.0.0",
            target="pre_compile_patch",
            context={"compile_error": "stub"},
        )


def test_target_handlers_requires_named_function_to_exist(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={
            "migrate_to_1_0_0.py": (
                "TARGET_HANDLERS = {'pre_compile_patch': 'patch_pre_compile'}\n"
                "\n"
                "def migrate(vault_root):\n"
                "    return {'status': 'ok'}\n"
            )
        },
    )
    vault = _make_vault(tmp_path, "0.9.0")
    shutil.copytree(source / "scripts" / "migrations", vault / ".brain-core" / "scripts" / "migrations")

    with pytest.raises(
        upgrade.MigrationDefinitionError,
        match=r"maps 'pre_compile_patch' to missing function 'patch_pre_compile'",
    ):
        upgrade._run_migrations(
            str(vault),
            "0.9.0",
            "1.0.0",
            target="pre_compile_patch",
            context={"compile_error": "stub"},
        )


def test_target_handler_discovery_rereads_rewritten_file(tmp_path):
    script = tmp_path / "migrate_to_1_0_0.py"
    script.write_text(
        "TARGET_HANDLERS = {'pre_compile_patch': 'patch_pre_compile'}\n"
        "\n"
        "def patch_pre_compile(vault_root, *, context=None):\n"
        "    return {'status': 'ok'}\n"
    )

    first = upgrade._extract_target_handler_names(str(script))

    script.write_text(
        "TARGET_HANDLERS = {'pre_compile_patch': 'other_handler'}\n"
        "\n"
        "def other_handler(vault_root, *, context=None):\n"
        "    return {'status': 'ok'}\n"
    )

    second = upgrade._extract_target_handler_names(str(script))

    assert first == {"pre_compile_patch": "patch_pre_compile"}
    assert second == {"pre_compile_patch": "other_handler"}


def test_upgrade_rolls_back_precompile_patch_failure(tmp_path):
    source = _make_source(
        tmp_path,
        "1.0.0",
        migrations={
            "migrate_to_1_0_0.py": (
                "import os\n"
                "TARGET_HANDLERS = {'pre_compile_patch': 'patch_pre_compile'}\n"
                "\n"
                "def patch_pre_compile(vault_root, *, context=None):\n"
                "    path = os.path.join(vault_root, '.brain', 'local', 'touched.txt')\n"
                "    with open(path, 'w', encoding='utf-8') as f:\n"
                "        f.write('partial')\n"
                "    raise RuntimeError('boom')\n"
            )
        },
    )
    vault = _make_vault(tmp_path, "0.9.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error"
    assert "pre-compile patch failed" in result["message"]
    assert not (vault / ".brain" / "local" / "touched.txt").exists()
    assert not (vault / ".brain" / "local" / "migrations.json").exists()
    assert (vault / ".brain-core" / "VERSION").read_text().strip() == "0.9.0"


def test_upgrade_runs_real_mcp_transport_repair_migration(tmp_path):
    source = _make_source(tmp_path, "0.27.6", migrations=None)
    vault = _make_vault(tmp_path, "0.27.5")
    project_config = vault / ".mcp.json"
    legacy = {
        "command": "/usr/bin/python3",
        "args": [
            str(vault / ".brain-core" / "mcp" / "proxy.py"),
            "/usr/bin/python3",
            str(vault / ".brain-core" / "mcp" / "server.py"),
        ],
        "env": {"BRAIN_VAULT_ROOT": str(vault)},
    }
    project_config.write_text(json.dumps({"mcpServers": {"brain": legacy}}, indent=2) + "\n")
    (vault / ".brain" / "local" / "init-state.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "client": "claude",
                        "scope": "project",
                        "target_path": str(vault),
                        "config_path": str(project_config),
                        "server_name": "brain",
                        "server_config": legacy,
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )

    result = upgrade.upgrade(str(vault), str(source), sync=False)
    repaired = json.loads(project_config.read_text())["mcpServers"]["brain"]
    ledger = _ledger(vault)

    assert result["status"] == "ok"
    assert [item["version"] for item in result["migrations"]] == ["0.27.6"]
    assert repaired["args"] == [
        "-m",
        "brain_mcp.proxy",
        "/usr/bin/python3",
        "brain_mcp.server",
    ]
    assert repaired["env"]["PYTHONPATH"] == str(vault / ".brain-core")
    assert repaired["env"]["BRAIN_WORKSPACE_DIR"] == str(vault)
    assert ledger["migrations"]["0.27.6"]["status"] == "ok"
