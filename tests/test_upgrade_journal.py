"""A killed upgrade is restored by the next run from its rollback journal (DD-085).

Real files under an isolated state home (``XDG_STATE_HOME``, set for every
test by ``conftest``): a run killed at any pre-commit point leaves a journal
that the next run restores before it proceeds; a committed run leaves none; a
leftover journal is classified by the VERSION witness; a dry run only reports;
an untrustworthy journal is a no-effect refusal; the vault lock excludes a
concurrent upgrade; a restore never destroys bytes it did not journal; and the
journal reads only what the run can change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading

import pytest

from _bootstrap import upgrade_journal
from _bootstrap.upgrade_journal import RecoveryStore, UpgradeJournal
import upgrade
from test_upgrade_migrations import _REAL_SCRIPTS, _counter_migration, _make_source, _make_vault

PRE = upgrade_journal.STAGE_PRE_COMPILE
POST = upgrade_journal.STAGE_POST_COMPILE


@pytest.fixture(autouse=True)
def _close_machine_state(tmp_path, monkeypatch, fake_home):
    """Keep the runs off the developer's CLI, runtime and MCP state."""
    from _bootstrap import mcp_transport

    monkeypatch.setattr(mcp_transport, "delegate_machine_command", lambda *args, **kwargs: {"status": "ok", "committed_effects": []})
    monkeypatch.setattr(upgrade, "CLI_TARGET_LOCATIONS", (tmp_path / "machine" / "bin" / "brain",))
    monkeypatch.setattr(
        upgrade,
        "_ensure_central_runtime",
        lambda _vault, *, requirements_changed, sync_deps: {"outcome": upgrade.RUNTIME_REUSED, "requirements_changed": requirements_changed},
    )
    monkeypatch.setattr(upgrade, "_complete_runtime_readiness", lambda _vault: {"outcome": "ok", "message": "ready"})
    monkeypatch.setattr(upgrade, "_inspect_runtime_orphans", lambda _vault: {"outcome": "ok", "orphan_candidates": 0, "message": "tidy"})


_KILLED_MIGRATION = (
    "import os\n"
    "\n"
    "def migrate(vault_root):\n"
    "    local = os.path.join(vault_root, '.brain', 'local')\n"
    "    with open(os.path.join(local, 'count.txt'), 'w') as f:\n"
    "        f.write('1')\n"
    "    with open(os.path.join(vault_root, '.brain', 'config.yaml'), 'w') as f:\n"
    "        f.write('half: written\\n')\n"
    "    os._exit(9)\n"
)


def _journal_dir(vault: Path) -> Path:
    return Path(upgrade_journal.journal_directory(str(vault)))


def _recovery_root(vault: Path) -> Path:
    return Path(upgrade_journal.recovery_root(str(vault)))


def _warning(result: dict, code: str) -> dict:
    return next(w for w in result.get("warnings", []) if w.get("code") == code)


def _codes(result: dict) -> set[str]:
    return {w["code"] for w in result.get("warnings", []) if "code" in w}


def _version(vault: Path) -> str:
    return (vault / ".brain-core" / "VERSION").read_text().strip()


def _vault_bytes(root: Path) -> dict[str, bytes]:
    """Every file but lock endpoints, the one thing a refused run may create."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".lock"
    }


def _install_core(vault: Path, source: Path) -> None:
    shutil.rmtree(vault / ".brain-core")
    shutil.copytree(source, vault / ".brain-core")


def _run_upgrade_in_subprocess(vault: Path, source: Path, prelude: str = "") -> subprocess.CompletedProcess:
    """One upgrade in its own process, so a kill is a real ``os._exit`` and no rollback runs."""
    script = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(_REAL_SCRIPTS)!r})\n"
        "import upgrade\n"
        "from _bootstrap import upgrade_journal\n"
        + prelude
        + f"print(upgrade.upgrade({str(vault)!r}, {str(source)!r}, sync=False, sync_deps=False))\n"
    )
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=os.environ.copy())


def _leftover_journal(vault: Path, old: str, new: str, path: Path) -> UpgradeJournal:
    """A journal holding ``path``'s current bytes; the caller then changes the file."""
    journal = UpgradeJournal.open(str(vault), old, new)
    journal.capture(PRE, [str(path)])
    return journal


def _recovery_manifest(vault: Path) -> tuple[Path, dict]:
    (directory,) = [path for path in _recovery_root(vault).iterdir() if path.is_dir()]
    return directory, json.loads((directory / "manifest.json").read_text())


# --- a kill is a failure the next run undoes --------------------------------------

def test_a_run_killed_mid_migration_is_restored_and_completed_by_the_next_run(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _KILLED_MIGRATION})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "config.yaml").write_text("original: true\n")

    proc = _run_upgrade_in_subprocess(vault, source)

    assert proc.returncode == 9, proc.stderr
    assert (vault / ".brain" / "local" / "count.txt").read_text() == "1", "the kill left the migration half applied"
    assert (vault / ".brain" / "config.yaml").read_text() == "half: written\n"
    assert _version(vault) == "1.0.0"
    assert (_journal_dir(vault) / "journal.json").is_file(), "no rollback ran, the journal survived"

    (source / "scripts" / "migrations" / "migrate_to_2_0_0.py").write_text(_counter_migration("count.txt"))
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    recovered = _warning(result, "recovered_interrupted_upgrade")
    assert "1.0.0 → 2.0.0" in recovered["message"] and "during post_compile_migrations" in recovered["message"]
    assert "has restored the vault" in recovered["message"]
    assert "interrupted_previous_upgrade" not in _codes(result), "one story, not two"
    assert [w["code"] for w in result["warnings"]].count("recovered_interrupted_upgrade") == 1
    assert result["recovery"]["action"] == "restored"
    _directory, manifest = _recovery_manifest(vault)
    assert {os.path.relpath(path, vault) for path in manifest["preserved"]} == {
        ".brain/local/count.txt", ".brain/config.yaml", ".brain/local/compiled-router.json",
    }, "the run's own half-applied bytes are kept, not destroyed"
    assert (vault / ".brain" / "config.yaml").read_text() == "original: true\n"
    assert (vault / ".brain" / "local" / "count.txt").read_text() == "1", "the migration reran once from the restored state"
    assert _version(vault) == "2.0.0"
    assert not _journal_dir(vault).exists()


def test_a_committed_run_leaves_no_journal(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert "recovery" not in result
    assert not _journal_dir(vault).exists()


def test_an_in_process_failure_still_restores_and_discards_the_journal(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_2_0_0.py": "def migrate(vault_root):\n    open(vault_root + '/.brain/config.yaml', 'w').write('x')\n    raise RuntimeError('boom')\n",
    })
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "config.yaml").write_text("original: true\n")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["rollback_verified"] is True, result
    assert (vault / ".brain" / "config.yaml").read_text() == "original: true\n"
    assert not _journal_dir(vault).exists()


_PRECOMPILE_KILL = (
    "import os\n"
    "TARGET_HANDLERS = {'pre_compile_patch': 'patch'}\n"
    "def patch(vault_root, *, context=None):\n"
    "    open(os.path.join(vault_root, '_Config', 'new.md'), 'w').write('x')\n"
    "    open(os.path.join(vault_root, '_Config', 'router.md'), 'w').write('mangled')\n"
    "    os._exit(9)\n"
    "def migrate(vault_root):\n"
    "    return {'status': 'skipped'}\n"
)


@pytest.mark.parametrize("site", ["precompile_config", "skills", "before_version", "recovery"])
def test_a_kill_at_every_pre_commit_point_is_undone_by_the_next_run(tmp_path, site):
    """Real ``os._exit`` kills: an in-process exception rolls back, a kill must not."""
    migrations = {"migrate_to_2_0_0.py": _counter_migration("count.txt")}
    if site == "precompile_config":
        migrations["migrate_to_1_5_0.py"] = _PRECOMPILE_KILL
    source = _make_source(tmp_path, "2.0.0", migrations=migrations)
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "config.yaml").write_text("original: true\n")
    skill = vault / "_Config" / "Skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("orig\n")
    before = _vault_bytes(vault)
    prelude = ""
    if site == "skills":
        prelude = (
            "import _skill_library\n"
            "_skill_library.preview_core_override_reconciliation = lambda v, *a, **k: ['demo']\n"
            "def _kill(v, *a, **k):\n"
            f"    open({str(skill / 'SKILL.md')!r}, 'w').write('mangled')\n"
            f"    open({str(skill / 'extra.md')!r}, 'w').write('x')\n"
            "    os._exit(9)\n"
            "_skill_library.reconcile_core_overrides = _kill\n"
        )
    if site == "before_version":
        prelude = "upgrade._commit_version = lambda *a, **k: os._exit(9)\n"
    if site == "recovery":
        (source / "scripts" / "migrations" / "migrate_to_2_0_0.py").write_text(_KILLED_MIGRATION)

    proc = _run_upgrade_in_subprocess(vault, source, prelude)
    assert proc.returncode == 9, proc.stderr
    assert _version(vault) == "1.0.0"

    if site == "recovery":
        # The next run is killed while restoring, after the ledger-first pass.
        (source / "scripts" / "migrations" / "migrate_to_2_0_0.py").write_text(_counter_migration("count.txt"))
        proc = _run_upgrade_in_subprocess(vault, source, (
            "real = upgrade_journal.restore_snapshots\n"
            "def killed(snapshots, **kwargs):\n"
            "    if kwargs.get('roots'):\n"
            "        os._exit(7)\n"
            "    return real(snapshots, **kwargs)\n"
            "upgrade_journal.restore_snapshots = killed\n"
        ))
        assert proc.returncode == 7, proc.stderr
        assert (_journal_dir(vault) / "journal.json").is_file(), "an interrupted restore keeps its journal"
    if site == "precompile_config":
        (source / "scripts" / "migrations" / "migrate_to_1_5_0.py").write_text(
            "TARGET_HANDLERS = {'pre_compile_patch': 'patch'}\n"
            "def patch(vault_root, *, context=None):\n    return {'status': 'skipped'}\n"
            "def migrate(vault_root):\n    return {'status': 'skipped'}\n"
        )

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert _version(vault) == "2.0.0"
    assert not _journal_dir(vault).exists()
    if site == "before_version":
        # The journal closed before the kill: the content is committed under the
        # old VERSION, the ledger records the migration, and the rerun finishes it.
        assert "interrupted_previous_upgrade" in _codes(result)
        assert "recovered_interrupted_upgrade" not in _codes(result)
        assert "migrations" not in result, "the recorded migration does not rerun"
        assert (vault / ".brain" / "local" / "count.txt").read_text() == "1"
        return
    assert "recovered_interrupted_upgrade" in _codes(result)
    assert [item["version"] for item in result["migrations"] if item["version"] == "2.0.0"] == ["2.0.0"]
    assert (vault / ".brain" / "local" / "count.txt").read_text() == "1", "the migration reran once from the restored state"
    assert (vault / ".brain" / "config.yaml").read_text() == "original: true\n"
    assert (vault / "_Config" / "router.md").read_bytes() == before["_Config/router.md"]
    assert not (vault / "_Config" / "new.md").exists()
    assert (skill / "SKILL.md").read_text() == "orig\n" and not (skill / "extra.md").exists()


def test_a_declared_directory_effect_is_journalled_as_a_tree(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": (
        "import os\n"
        "def prospective_effects(vault_root):\n"
        "    return [os.path.join(vault_root, 'Ideas')]\n"
        "def migrate(vault_root):\n"
        "    os.makedirs(os.path.join(vault_root, 'Ideas', 'sub'))\n"
        "    open(os.path.join(vault_root, 'Ideas', 'sub', 'new.md'), 'w').write('x')\n"
        "    open(os.path.join(vault_root, 'Ideas', 'a.md'), 'w').write('changed')\n"
        "    raise RuntimeError('boom')\n"
    )})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / "Ideas").mkdir()
    (vault / "Ideas" / "a.md").write_text("orig")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["rollback_verified"] is True, result
    assert (vault / "Ideas" / "a.md").read_text() == "orig"
    assert not (vault / "Ideas" / "sub").exists(), "entries the migration created under the declared directory are pruned"


def test_released_declarations_name_the_directories_and_router_line_they_change(tmp_path):
    import migrate_to_0_24_0
    import migrate_to_0_25_0
    import migrate_to_0_67_0

    vault = _make_vault(tmp_path, "0.66.0")
    month = vault / "_Temporal" / "Logs" / "2026-03"
    month.mkdir(parents=True)
    (month / "20260301-log.md").write_text("---\ntype: temporal/log\n---\n")
    router = str((vault / "_Config" / "router.md").resolve())

    assert router in migrate_to_0_24_0.prospective_effects(str(vault))
    assert router in migrate_to_0_25_0.prospective_effects(str(vault))
    assert str(vault / "_Temporal") in migrate_to_0_67_0.prospective_effects(str(vault))


# --- classifying a leftover journal ------------------------------------------------

def test_a_leftover_journal_of_a_committed_version_changing_run_is_discarded_not_restored(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    config = vault / ".brain" / "notes.txt"
    config.write_text("before\n")
    _leftover_journal(vault, "1.0.0", "2.0.0", config)
    config.write_text("after\n")

    result = upgrade.upgrade(str(vault), str(source), force=True, sync=False)

    assert result["status"] == "ok", result
    assert "VERSION witnesses" in _warning(result, "upgrade_journal_discarded")["message"]
    assert result["recovery"]["action"] == "discarded"
    assert config.read_text() == "after\n", "VERSION witnesses the commit, so the journal is stale"
    assert not _journal_dir(vault).exists()


def test_a_leftover_journal_of_a_same_version_run_is_restored(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    config = vault / ".brain" / "notes.txt"
    config.write_text("before\n")
    _leftover_journal(vault, "2.0.0", "2.0.0", config)
    config.write_text("after\n")

    result = upgrade.upgrade(str(vault), str(source), force=True, sync=False)

    assert result["status"] == "ok", result
    assert "2.0.0 → 2.0.0" in _warning(result, "recovered_interrupted_upgrade")["message"]
    assert config.read_text() == "before\n", "VERSION cannot witness a same-version run; its effects are re-applicable"
    assert not _journal_dir(vault).exists()


def test_a_journal_whose_vault_has_moved_on_to_a_third_version_is_refused_as_stale(tmp_path):
    """A 1.0 → 2.0 journal must not restore 1.0-era state over a vault that reached 3.0 another way."""
    vault = _make_vault(tmp_path, "1.0.0")
    ledger = vault / ".brain" / "local" / "migrations.json"
    router = vault / "_Config" / "router.md"
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")
    journal.capture(PRE, [str(ledger)])
    journal.capture_tree(PRE, str(vault / ".brain"))
    journal.capture_tree(PRE, str(vault / "_Config"))
    parked = tmp_path / "parked-journal"
    shutil.move(journal.directory, parked)
    reached = _make_source(tmp_path, "3.0.0", migrations={
        "migrate_to_2_0_0.py": _counter_migration("two.txt"), "migrate_to_3_0_0.py": _counter_migration("three.txt"),
    })
    assert upgrade.upgrade(str(vault), str(reached), sync=False)["status"] == "ok"
    router.write_text("edited at 3.0\n")
    shutil.move(parked, journal.directory)
    before = _vault_bytes(vault)
    next_source = _make_source(tmp_path, "3.1.0", migrations={
        "migrate_to_2_0_0.py": _counter_migration("two.txt"), "migrate_to_3_0_0.py": _counter_migration("three.txt"),
        "migrate_to_3_1_0.py": _counter_migration("thirtyone.txt"),
    })

    result = upgrade.upgrade(str(vault), str(next_source), sync=False)
    preview = upgrade.upgrade(str(vault), str(next_source), dry_run=True, sync=False)

    assert result["status"] == "error" and result["reason"] == "journal_stale", result
    assert result["rollback_verified"] is True and preview == result
    assert journal.directory in result["message"]
    assert "1.0.0 → 2.0.0" in result["message"] and "3.0.0" in result["message"] and "move the journal aside" in result["message"]
    assert _vault_bytes(vault) == before, "nothing was restored over the newer content"
    assert set(json.loads(ledger.read_text())["migrations"]) >= {"2.0.0", "3.0.0"}
    assert (Path(journal.directory) / "journal.json").is_file()


def test_a_dry_run_reports_the_pending_rollback_and_writes_nothing(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    config = vault / ".brain" / "notes.txt"
    config.write_text("before\n")
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", config)
    config.write_text("after\n")
    before = _vault_bytes(vault)

    preview = upgrade.upgrade(str(vault), str(source), dry_run=True, sync=False)

    assert preview["status"] == "ok", preview
    assert "will restore the vault" in _warning(preview, "recovered_interrupted_upgrade")["message"]
    assert "recovery" not in preview, "a dry run performs no recovery"
    assert _vault_bytes(vault) == before
    assert (Path(journal.directory) / "journal.json").is_file()


def test_a_dry_run_previews_the_migrations_the_restored_ledger_will_rerun(tmp_path):
    """The killed run recorded 2.0.0 before dying; the restore unrecords it, and the preview says so."""
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    ledger = vault / ".brain" / "local" / "migrations.json"
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")
    journal.capture(PRE, [str(ledger)])
    ledger.write_text(json.dumps({"schema_version": 1, "migrations": {"2.0.0": {"status": "ok"}, "3.0.0": {"status": "ok"}}}))

    preview = upgrade.upgrade(str(vault), str(source), dry_run=True, sync=False)

    assert preview["status"] == "ok", "the content guard judges the ledger the restore will leave, not the killed run's"
    assert [item["version"] for item in preview["migrations_preview"]] == ["2.0.0"]
    assert ledger.exists(), "nothing was written"


def test_a_dry_run_leaves_a_committed_leftover_journal_in_place(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", vault / ".brain" / "preferences.json")

    preview = upgrade.upgrade(str(vault), str(source), force=True, dry_run=True, sync=False)

    assert preview["status"] == "ok" and "upgrade_journal_discarded" not in _codes(preview)
    assert (Path(journal.directory) / "journal.json").is_file()


def test_a_headerless_remnant_is_never_a_journal_and_the_running_log_still_warns(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    remnant = _journal_dir(vault)
    (remnant / "blobs").mkdir(parents=True)
    (remnant / "entries.jsonl").write_bytes(b'{"stage": "pre_compile", "path": "/x", "exists": false, "blob": null}\n')
    (vault / ".brain" / "local" / "last-upgrade.json").write_text(json.dumps({
        "status": "running", "stage": "copy_brain_core", "old_version": "1.0.0", "new_version": "2.0.0",
    }))

    preview = upgrade.upgrade(str(vault), str(source), dry_run=True, sync=False)

    assert UpgradeJournal.load(str(vault)) is None
    assert "No rollback journal was found" in _warning(preview, "interrupted_previous_upgrade")["message"]
    assert remnant.exists(), "a dry run removes nothing"

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert not remnant.exists()


# --- an untrustworthy journal is refused -----------------------------------------------

@pytest.mark.parametrize("damage", ["corrupt-header", "another-vault", "missing-blob", "wrong-blob-bytes", "root-outside-vault"])
def test_an_untrustworthy_journal_is_refused_with_no_effect(tmp_path, damage):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    config = vault / ".brain" / "notes.txt"
    config.write_text("before\n")
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", config)
    header = Path(journal.directory) / "journal.json"
    blobs = Path(journal.directory) / "blobs"
    if damage == "corrupt-header":
        header.write_text("{not json")
    elif damage == "another-vault":
        header.write_text(json.dumps({**json.loads(header.read_text()), "vault": str(tmp_path / "elsewhere")}))
    elif damage == "missing-blob":
        shutil.rmtree(blobs)
    elif damage == "wrong-blob-bytes":
        (blob,) = blobs.iterdir()
        blob.write_bytes(b"not the captured bytes\n")
    else:
        with open(Path(journal.directory) / "entries.jsonl", "a") as handle:
            handle.write(json.dumps({"stage": PRE, "root": str(tmp_path / "elsewhere"), "dirs": []}) + "\n")
    config.write_text("after\n")
    before = _vault_bytes(vault)

    result = upgrade.upgrade(str(vault), str(source), sync=False)
    preview = upgrade.upgrade(str(vault), str(source), dry_run=True, sync=False)

    assert result["status"] == "error" and result["reason"] == "journal_unreadable", result
    assert result["rollback_verified"] is True
    assert journal.directory in result["message"] and "move it aside" in result["message"]
    if damage == "wrong-blob-bytes":
        # Blob content is checked against its name when it is read; a dry run reads none.
        assert preview["status"] == "ok" and "recovered_interrupted_upgrade" in _codes(preview)
    else:
        assert preview == result
    assert _vault_bytes(vault) == before
    assert header.exists(), "the journal is retained for recovery by hand"


def test_opening_a_journal_refuses_one_already_present(tmp_path):
    vault = _make_vault(tmp_path, "1.0.0")
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", vault / ".brain" / "preferences.json")
    entries = (Path(journal.directory) / "entries.jsonl").read_bytes()

    with pytest.raises(FileExistsError):
        UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")

    assert (Path(journal.directory) / "entries.jsonl").read_bytes() == entries


def test_a_journal_that_cannot_be_opened_is_a_no_effect_refusal(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    blocker = tmp_path / "state-is-a-file"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    before = _vault_bytes(vault)

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["reason"] == "journal_unavailable", result
    assert result["rollback_verified"] is True
    assert _vault_bytes(vault) == before, "neither the progress log nor the template capture was written"


# --- a restore never destroys bytes it did not journal ---------------------------------

_KILL_IN_IDEAS = (
    "import os\n"
    "def migrate(vault_root):\n"
    "    open(os.path.join(vault_root, 'Ideas', 'a.md'), 'w').write('migrated')\n"
    "    os._exit(9)\n"
)


def test_edits_made_after_a_kill_are_preserved_before_the_restore_replaces_them(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _KILL_IN_IDEAS})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / "Ideas").mkdir()
    (vault / "Ideas" / "a.md").write_text("orig")
    (vault / ".brain" / "local" / "compiled-router.json").write_text(json.dumps({"artefacts": [{"path": "Ideas"}]}))
    assert _run_upgrade_in_subprocess(vault, source).returncode == 9
    edits = {
        "Ideas/new-note.md": b"days of work\n",
        "_Config/router.md": b"user edited router\n",
        ".brain/local/config.yaml": b"defaults:\n  access:\n    initial:\n      mode: explicit\n      commands: []\n",
    }
    for relative, content in edits.items():
        (vault / relative).write_bytes(content)
    (source / "scripts" / "migrations" / "migrate_to_2_0_0.py").write_text("def migrate(vault_root):\n    return {'status': 'ok'}\n")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert (vault / "Ideas" / "a.md").read_text() == "orig"
    assert (vault / "_Config" / "router.md").read_text() == "Brain vault.\n"
    assert not (vault / "Ideas" / "new-note.md").exists() and not (vault / ".brain" / "local" / "config.yaml").exists()
    directory, manifest = _recovery_manifest(vault)
    preserved = {
        os.path.relpath(path, vault): (directory / copy).read_bytes() for path, copy in manifest["preserved"].items()
    }
    assert {key: preserved[key] for key in edits} == edits, "every changed byte is kept, byte for byte"
    assert preserved[os.path.join("Ideas", "a.md")] == b"migrated"
    kept = len(manifest["preserved"])
    assert kept == len(edits) + 1, "the edits and the migration's own write"
    assert result["recovery"] == {
        "action": "restored", "journal": str(_journal_dir(vault)), "restored_paths": kept,
        "preserved_paths": kept, "recovery_directory": str(directory),
    }
    warning = _warning(result, "recovered_interrupted_upgrade")
    assert f"{kept} paths that had changed since were kept under {directory}" in warning["message"]


def test_an_untouched_file_is_not_rewritten_by_the_restore(tmp_path):
    vault = _make_vault(tmp_path, "1.0.0")
    untouched = vault / ".brain" / "preferences.json"
    changed = vault / ".brain" / "tracking.json"
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")
    journal.capture_tree(PRE, str(vault / ".brain"))
    inode = untouched.stat().st_ino
    changed.write_text("changed\n")

    report = upgrade_journal.restore_journal(journal, str(vault / ".brain" / "local" / "migrations.json"), RecoveryStore.for_vault(str(vault)))

    assert report.verified and report.restored_paths == {str(changed)}
    assert untouched.stat().st_ino == inode, "an unchanged path keeps its inode"
    assert changed.read_text() == "{}\n"


def test_an_in_process_rollback_keeps_what_it_overwrites_and_reports_the_journal_when_it_cannot_read_it(tmp_path):
    """A rollback that cannot read its journal still restores the Core and retains both for recovery by hand."""
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": (
        "import os, shutil\n"
        "from _bootstrap.upgrade_journal import journal_directory\n"
        "def migrate(vault_root):\n"
        "    open(os.path.join(vault_root, '.brain', 'config.yaml'), 'w').write('x')\n"
        "    shutil.rmtree(os.path.join(journal_directory(vault_root), 'blobs'))\n"
        "    raise RuntimeError('boom')\n"
    )})
    (source / "extra-core.md").write_text("new core file\n")
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "config.yaml").write_text("original: true\n")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["rollback_verified"] is False, result
    assert result["rollback"]["brain_core"] == "restored" and result["rollback"]["vault_state"] == "unverified"
    assert not (vault / ".brain-core" / "extra-core.md").exists() and _version(vault) == "1.0.0"
    assert any(error.startswith("rollback journal: ") for error in result["rollback"]["errors"])
    assert {str(_journal_dir(vault)), result["rollback"]["recovery_backup"]} <= set(result["recovery_paths"])
    assert (_journal_dir(vault) / "journal.json").is_file()


# --- the torn tail of a batch --------------------------------------------------------------

def test_a_torn_tree_batch_restores_what_it_holds_and_removes_nothing(tmp_path):
    """Truncated at every byte boundary of its entries, a journal never deletes a file the run did not touch."""
    vault = _make_vault(tmp_path, "1.0.0")
    ideas = vault / "Ideas"
    ideas.mkdir()
    for index in range(3):
        (ideas / f"n{index}.md").write_text(f"note {index}")
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")
    journal.capture(PRE, [str(vault / ".brain" / "local" / "migrations.json")])
    journal.capture_tree(POST, str(ideas))
    entries = Path(journal.directory) / "entries.jsonl"
    complete = entries.read_bytes()
    lines = complete.split(b"\n")[:-1]
    assert json.loads(lines[-1]).get("root") == str(ideas), "the root line is written last"
    before = _vault_bytes(vault)
    cuts = {0, len(complete)}
    offset = 0
    for line in lines:
        offset += len(line) + 1
        cuts.update({offset - 1, offset, min(offset + 10, len(complete))})

    for cut in sorted(cuts):
        entries.write_bytes(complete[:cut])
        loaded = UpgradeJournal.load(str(vault))
        report = upgrade_journal.restore_journal(loaded, str(vault / ".brain" / "local" / "migrations.json"), RecoveryStore.for_vault(str(vault)))
        assert report.verified, (cut, report)
        assert _vault_bytes(vault) == before, f"cut at {cut} removed or changed an untouched file"


# --- the lock -------------------------------------------------------------------------

def _hold_lock(vault: Path):
    from _bootstrap.file_lock import vault_mutation_lock

    held, release = threading.Event(), threading.Event()

    def hold():
        with vault_mutation_lock(vault):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(10)
    return release, holder


def test_a_concurrent_upgrade_is_refused_while_the_vault_is_locked(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    notes = vault / ".brain" / "notes.txt"
    notes.write_text("before\n")
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", notes)
    notes.write_text("after\n")
    before = _vault_bytes(vault)
    monkeypatch.setattr(upgrade, "_UPGRADE_LOCK_TIMEOUT", 0.2)
    release, holder = _hold_lock(vault)
    try:
        result = upgrade.upgrade(str(vault), str(source), sync=False)
    finally:
        release.set()
        holder.join(10)

    assert result["status"] == "error" and result["reason"] == "vault_busy", result
    assert result["rollback_verified"] is True and "recovery" not in result
    assert _vault_bytes(vault) == before, "the leftover journal is not recovered under another holder's lock"
    assert (Path(journal.directory) / "journal.json").is_file()


def test_a_lock_that_cannot_be_taken_is_the_same_refusal(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "local" / "mutation.lock").mkdir()

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["reason"] == "vault_busy", result
    assert "mutation lock could not be taken" in result["message"]


def test_the_upgrader_holds_the_lock_across_skill_reconciliation(tmp_path, monkeypatch):
    import _skill_library

    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    seen = {}

    def observe(vault_root, *, lock_held=False):
        seen["lock_held"] = lock_held
        return ()

    monkeypatch.setattr(_skill_library, "reconcile_core_overrides", observe)

    assert upgrade.upgrade(str(vault), str(source), sync=False)["status"] == "ok"
    assert seen == {"lock_held": True}, "the lock is not re-entrant, so the upgrader says it holds it"


# --- recovery is an effect, whatever follows --------------------------------------------

def test_a_refused_run_that_recovered_a_journal_reports_the_recovery(tmp_path):
    older = _make_source(tmp_path, "0.9.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    notes = vault / ".brain" / "notes.txt"
    notes.write_text("before\n")
    _leftover_journal(vault, "1.0.0", "2.0.0", notes)
    notes.write_text("after\n")

    result = upgrade.upgrade(str(vault), str(older), sync=False)

    assert result["status"] == "error" and result["reason"] == "content_ahead", result
    assert result["rollback_verified"] is True
    assert notes.read_text() == "before\n", "the restore happened before the guard refused"
    assert result["recovery"]["action"] == "restored" and _journal_dir(vault) == Path(result["recovery"]["journal"])
    assert "recovered_interrupted_upgrade" in _codes(result)
    assert not _journal_dir(vault).exists()


def test_a_skipped_run_that_recovered_a_journal_reports_the_recovery(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    notes = vault / ".brain" / "notes.txt"
    notes.write_text("before\n")
    _leftover_journal(vault, "2.0.0", "2.0.0", notes)
    notes.write_text("after\n")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "skipped", result
    assert notes.read_text() == "before\n"
    assert result["recovery"]["action"] == "restored" and "recovered_interrupted_upgrade" in _codes(result)


@pytest.mark.parametrize("witnessed", [True, False])
def test_a_journal_that_cannot_be_discarded_is_reported_not_raised(tmp_path, witnessed):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0" if witnessed else "1.0.0")
    if witnessed:
        _install_core(vault, source)
    notes = vault / ".brain" / "notes.txt"
    notes.write_text("before\n")
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", notes)
    notes.write_text("after\n")
    os.chmod(journal.directory, 0o500)
    try:
        result = upgrade.upgrade(str(vault), str(source), force=witnessed, sync=False)
    finally:
        os.chmod(journal.directory, 0o700)

    warning = _warning(result, "upgrade_journal_not_discarded")
    assert (Path(journal.directory) / "journal.json").is_file()
    assert result["status"] == "error" and result["reason"] == "journal_unavailable", "a journal that stays cannot be reopened"
    assert result["recovery"]["action"] == ("discarded" if witnessed else "restored")
    if witnessed:
        assert "VERSION witnesses this commit" in warning["message"]
        assert notes.read_text() == "after\n"
    else:
        assert notes.read_text() == "before\n"
        assert "restores the same bytes again" in warning["message"] and "discard" not in warning["message"]
    again = upgrade.upgrade(str(vault), str(source), force=witnessed, sync=False)
    assert again["status"] == "ok", again
    assert ("upgrade_journal_discarded" if witnessed else "recovered_interrupted_upgrade") in _codes(again)


def test_a_next_run_restore_that_does_not_verify_retains_the_journal(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    notes = vault / ".brain" / "notes.txt"
    notes.write_text("before\n")
    journal = _leftover_journal(vault, "1.0.0", "2.0.0", notes)
    notes.unlink()
    notes.mkdir()

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["rollback_verified"] is False, result
    assert "could not be rolled back" in result["message"]
    assert {journal.directory, str(notes)} <= set(result["recovery_paths"])
    assert (Path(journal.directory) / "journal.json").is_file()


# --- durability -------------------------------------------------------------------------------

def test_blob_then_blobs_directory_then_entry_line_are_durable_before_the_vault_write(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("directory fsync is a POSIX facility")
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": (
        "import os\n"
        "def prospective_effects(vault_root):\n"
        "    return [os.path.join(vault_root, 'Ideas', 'a.md')]\n"
        "def migrate(vault_root):\n"
        "    open(os.path.join(vault_root, 'Ideas', 'a.md'), 'w').write('changed')\n"
        "    return {'status': 'ok'}\n"
    )})
    vault = _make_vault(tmp_path, "1.0.0")
    target = vault / "Ideas" / "a.md"
    target.parent.mkdir()
    target.write_bytes(b"orig")
    events = []
    real_fsync = os.fsync

    def spying_fsync(fd):
        info = os.fstat(fd)
        events.append(((info.st_dev, info.st_ino), target.read_bytes()))
        return real_fsync(fd)

    monkeypatch.setattr(upgrade_journal.os, "fsync", spying_fsync)
    monkeypatch.setattr(upgrade, "_fsync_files", lambda paths: None)

    assert upgrade.upgrade(str(vault), str(source), sync=False)["status"] == "ok"

    import hashlib

    journal_dir = _journal_dir(vault)
    # The journal is discarded at the commit; the inodes were recorded on the way.
    assert not journal_dir.exists()
    blob_digest = hashlib.sha256(b"orig").hexdigest()
    pending = [(key, content) for key, content in events if content == b"orig"]
    assert pending, "every fsync of the capture happened before the vault write"
    assert all(content == b"orig" for _key, content in events[:len(pending)])
    monkeypatch.undo()
    # Replay the capture against a fresh journal to learn which inodes it syncs, in order.
    probe = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")
    target.write_bytes(b"orig")
    order = []
    monkeypatch.setattr(upgrade_journal.os, "fsync", lambda fd: order.append(os.fstat(fd).st_ino))
    probe.capture(POST, [str(target)])
    blob = Path(probe.directory) / "blobs" / blob_digest
    expected = [blob.stat().st_ino, (Path(probe.directory) / "blobs").stat().st_ino, (Path(probe.directory) / "entries.jsonl").stat().st_ino]
    assert order == expected, "blob, then its directory, then the entry line"


def test_a_new_journal_directory_is_durable_up_to_its_first_existing_parent(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("directory fsync is a POSIX facility")
    vault = _make_vault(tmp_path, "1.0.0")
    synced = []
    monkeypatch.setattr(upgrade_journal.os, "fsync", lambda fd: synced.append(os.fstat(fd).st_ino))

    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")

    chain = Path(journal.directory) / "blobs"
    expected = {chain.stat().st_ino}
    while chain != Path(os.environ["XDG_STATE_HOME"]).parent:
        chain = chain.parent
        expected.add(chain.stat().st_ino)
    assert expected <= set(synced)


# --- scope: the journal reads only what the run can change -------------------------------

def _unreadable(path: Path, content: bytes = b"opaque") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0)
    return path


@pytest.fixture
def _needs_permission_checks():
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("file permissions do not restrict this user")


def test_artefact_folders_are_not_read_unless_an_undeclared_migration_is_pending(tmp_path, _needs_permission_checks):
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "local" / "compiled-router.json").write_text(json.dumps({"artefacts": [{"path": "Notes"}]}))
    opaque = _unreadable(vault / "Notes" / "note.md")
    try:
        nothing_pending = _make_source(tmp_path, "2.0.0", migrations={})
        result = upgrade.upgrade(str(vault), str(nothing_pending), sync=False)
        assert result["status"] == "ok", result

        undeclared = _make_source(tmp_path, "3.0.0", migrations={"migrate_to_3_0_0.py": _counter_migration("count.txt")})
        result = upgrade.upgrade(str(vault), str(undeclared), sync=False)
        assert result["status"] == "error", "the broad scope was journalled, and so read"
        assert "Permission denied" in result["message"] and result["rollback_verified"] is True
        assert _version(vault) == "2.0.0"
    finally:
        opaque.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize("declaration", ["exact", "empty"])
def test_a_declared_migration_leaves_artefact_folders_and_config_unread(tmp_path, _needs_permission_checks, declaration):
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "local" / "compiled-router.json").write_text(json.dumps({"artefacts": [{"path": "Notes"}]}))
    opaque = [_unreadable(vault / "Notes" / "note.md"), _unreadable(vault / "_Config" / "opaque.md")]
    declared = (
        "    return [os.path.join(vault_root, '.brain', 'local', 'count.txt')]\n" if declaration == "exact"
        else "    return []\n"
    )
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_2_0_0.py": _counter_migration("count.txt") + "\ndef prospective_effects(vault_root):\n" + declared,
    })
    try:
        result = upgrade.upgrade(str(vault), str(source), sync=False)
    finally:
        for path in opaque:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert result["status"] == "ok", result


def test_an_undeclared_migration_without_a_compiled_router_rolls_back(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    (source / "scripts" / "compile_router.py").write_text("import sys; sys.exit(0)\n")
    vault = _make_vault(tmp_path, "1.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error" and result["rollback_verified"] is True, result
    assert "compiled router" in result["message"]
    assert not (vault / ".brain" / "local" / "count.txt").exists(), "the migration never ran unguarded"


def test_excluded_local_stores_are_neither_read_nor_rolled_back(tmp_path, _needs_permission_checks):
    vault = _make_vault(tmp_path, "1.0.0")
    local = vault / ".brain" / "local"
    opaque = [
        _unreadable(local / "retrieval-index.json"),
        _unreadable(local / "doc-embeddings.npy"),
        _unreadable(local / "embeddings-meta.json"),
        _unreadable(local / "semantic-models" / "model" / "rev" / "weights.bin"),
        _unreadable(local / "command-outcomes" / ("0" * 64 + ".json")),
        _unreadable(local / "diagnostics" / "operational.log"),
        _unreadable(local / "staging" / "draft.md"),
    ]
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_2_0_0.py": (
            "import os\n"
            "def migrate(vault_root):\n"
            "    local = os.path.join(vault_root, '.brain', 'local')\n"
            "    open(os.path.join(local, 'type-embeddings.npy'), 'w').write('rebuilt')\n"
            "    open(os.path.join(local, 'count.txt'), 'w').write('1')\n"
            "    raise RuntimeError('boom')\n"
        ),
    })
    try:
        result = upgrade.upgrade(str(vault), str(source), sync=False)
    finally:
        for path in opaque:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert result["status"] == "error" and result["rollback_verified"] is True, result
    assert all(path.exists() for path in opaque), "excluded stores are not removed as introduced files"
    assert (local / "type-embeddings.npy").read_text() == "rebuilt", "an output outside the journal's scope is left to its maintenance command"
    assert not (local / "count.txt").exists(), "everything else rolls back"


def test_the_exclusion_set_restates_the_canonical_store_constants():
    """The journal module stays self-contained, so its exclusions are pinned to the owners' constants."""
    from _application._managed_preparation import SIDECARS
    from _command_interface.receipts import RECEIPT_DIRECTORY
    from _common._operational_log import DIAGNOSTICS_REL
    from _search.paths import OUTPUT_PATH
    from _semantic.model import SEMANTIC_MODELS_DIR_REL
    from _semantic.runtime import EMBEDDINGS_META_REL
    from _staging import STAGING_DIR

    canonical = [
        *SIDECARS, OUTPUT_PATH, EMBEDDINGS_META_REL, SEMANTIC_MODELS_DIR_REL,
        str(RECEIPT_DIRECTORY), str(DIAGNOSTICS_REL), STAGING_DIR, upgrade._LAST_UPGRADE_FILE,
    ]
    assert all(Path(relative).parts[:2] == (".brain", "local") for relative in canonical)
    assert upgrade_journal.EXCLUDED_LOCAL == {Path(relative).name for relative in canonical}


def test_lock_endpoints_are_never_journalled(tmp_path):
    vault = _make_vault(tmp_path, "1.0.0")
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    (vault / ".brain" / "local" / "runtime-status.lock").write_text("pid=1\n")
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")

    journal.capture_tree(PRE, str(vault / ".brain"))
    snapshots, roots = journal.stage_snapshots(PRE)

    assert not any(path.endswith(".lock") for path in snapshots)
    assert str(vault / ".brain" / "local") in roots[str(vault / ".brain")]
    journal.discard()
    assert upgrade.upgrade(str(vault), str(source), sync=False)["status"] == "ok"


def test_identical_content_is_stored_once(tmp_path):
    vault = _make_vault(tmp_path, "1.0.0")
    for name in ("a.md", "b.md", "c.md"):
        (vault / ".brain" / name).write_text("same\n")
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")

    journal.capture_tree(PRE, str(vault / ".brain"))

    blobs = {path.read_bytes() for path in (Path(journal.directory) / "blobs").iterdir()}
    assert blobs == {b"same\n", b"{}\n"}, "one blob per distinct content"
    snapshots, _roots = journal.stage_snapshots(PRE)
    assert {snapshots[str(vault / ".brain" / name)]["content"] for name in ("a.md", "b.md", "c.md")} == {b"same\n"}


def test_every_unreleased_migration_declares_its_effects():
    """The broad scope is for legacy migrations; a new one declares what it changes, so no artefact folder is read for it."""
    import ast

    migrations = _REAL_SCRIPTS / "migrations"
    released = upgrade._parse_version((_REAL_SCRIPTS.parents[0] / "VERSION").read_text().strip())
    undeclared = []
    for path in sorted(migrations.glob("migrate_to_*.py")):
        version = upgrade.migration_file_version(path.name)
        if version is None or upgrade._parse_version(version) <= released:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if "prospective_effects" not in upgrade._top_level_function_names(tree):
            undeclared.append(path.name)
    assert undeclared == []


# --- 0.68.0: the one migration that can grant a control command ------------------------

_OLD_TEMPLATE = {
    "vault": {
        "access": {"elevation_policy": "automatic", "default_lease_seconds": 900},
        "profiles": {
            "reader": {"allow": ["access.request", "vault.read-file"]},
            "operator": {"allow": ["access.request", "vault.read-file", "artefact.create"]},
        },
    },
    "defaults": {"access": {"initial_profile": "reader"}},
}
_NEW_TEMPLATE = {
    "vault": {
        "access": {"request_policy": "allowed"},
        "profiles": {
            "reader": {"allow": ["access.prepare", "access.request", "vault.read-file"]},
            "operator": {"allow": ["access.prepare", "access.request", "vault.read-file", "artefact.create"]},
        },
    },
    "defaults": {"access": {"initial": {"mode": "normal"}, "overrides": {}}},
}


def _authorisation_vault(root: Path):
    from _common._yaml import dump_mapping_text

    for part in (".brain-core/defaults", ".brain/local"):
        (root / part).mkdir(parents=True)
    (root / ".brain-core" / "defaults" / "config.yaml").write_text(dump_mapping_text(_OLD_TEMPLATE))
    (root / ".brain" / "config.yaml").write_text(dump_mapping_text({"vault": {
        "access": {"elevation_policy": "automatic"},
        "profiles": {"reader": _OLD_TEMPLATE["vault"]["profiles"]["reader"]},
    }}))
    (root / ".brain" / "local" / "config.yaml").write_text(dump_mapping_text(
        {"defaults": {"access": {"initial_profile": "reader"}}}
    ))
    return root


def _convert_authorisation(root: Path, monkeypatch, *, kill_at: int | None = None) -> dict:
    """One upgrade attempt as the runner sequences it: capture, copy, pre-compile patch."""
    from _common._yaml import dump_mapping_text
    from _command_interface.authorisation_migration import capture_legacy_authorisation, load_or_persist_template_capture
    import migrate_to_0_68_0

    template = load_or_persist_template_capture(root, "0.67.3")
    before = capture_legacy_authorisation(root, "0.67.3", template)
    (root / ".brain-core" / "defaults" / "config.yaml").write_text(dump_mapping_text(_NEW_TEMPLATE))
    writes = {"n": 0}

    def killing(real):
        def write(*args, **kwargs):
            writes["n"] += 1
            if writes["n"] == kill_at:
                raise KeyboardInterrupt(f"killed before write {kill_at}")
            return real(*args, **kwargs)
        return write

    with monkeypatch.context() as patched:
        patched.setattr(migrate_to_0_68_0, "safe_write", killing(migrate_to_0_68_0.safe_write))
        patched.setattr(migrate_to_0_68_0, "safe_write_json", killing(migrate_to_0_68_0.safe_write_json))
        try:
            return migrate_to_0_68_0.patch_pre_compile(root, context={"authorisation_before_upgrade": before})
        except KeyboardInterrupt:
            return {"killed": kill_at}


def _authorisation_layers(root: Path) -> dict:
    from _common._yaml import load_mapping_file

    return {name: load_mapping_file(root / name) for name in (".brain/config.yaml", ".brain/local/config.yaml")}


def test_the_planner_emits_the_local_write_before_the_shared_one(tmp_path):
    from _command_interface.authorisation_migration import capture_legacy_authorisation, load_or_persist_template_capture, plan_authorisation_migration

    root = _authorisation_vault(tmp_path / "vault")
    template = load_or_persist_template_capture(root, "0.67.3")
    before = capture_legacy_authorisation(root, "0.67.3", template)

    plan = plan_authorisation_migration(root, before, new_template=_NEW_TEMPLATE)

    assert [path for path, _content in plan.writes] == [".brain/local/config.yaml", ".brain/config.yaml"]


@pytest.mark.parametrize("kill_at", [2, 3])
def test_0_68_0_converges_when_killed_after_either_write_and_rerun_without_the_journal(tmp_path, monkeypatch, kill_at):
    reference = _authorisation_vault(tmp_path / "reference")
    assert _convert_authorisation(reference, monkeypatch)["status"] == "ok"
    killed = _authorisation_vault(tmp_path / "killed")

    assert _convert_authorisation(killed, monkeypatch, kill_at=kill_at) == {"killed": kill_at}
    assert _convert_authorisation(killed, monkeypatch)["status"] == "ok"

    assert _authorisation_layers(killed) == _authorisation_layers(reference)
    assert _authorisation_layers(killed)[".brain/local/config.yaml"]["defaults"]["access"]["initial"]["commands"] == [
        "access.request", "vault.read-file",
    ], "the local selection keeps the old reader's exact command set, never the converted shared profile"
