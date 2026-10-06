"""The killed-upgrade container gate: its kill prelude, its fixture guards and its reading of DD-085's recovery layout."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

import upgrade


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_ROOT = REPO_ROOT / "tools" / "brain-lab"
CONTAINER = TOOL_ROOT / "container"
SCRIPTS = REPO_ROOT / "src" / "brain-core" / "scripts"

# What the fake migration does when armed: two atomic replaces, as safe_write ends.
_FAKE_MIGRATION = (
    "import os, tempfile\n"
    "TARGET_HANDLERS = {'pre_compile_patch': 'patch'}\n"
    "def _write(path, text):\n"
    "    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))\n"
    "    with os.fdopen(fd, 'w') as handle:\n"
    "        handle.write(text)\n"
    "    os.replace(tmp, path)\n"
    "def migrate(vault_root):\n"
    "    _write(os.path.join(vault_root, 'first.md'), 'one')\n"
    "    _write(os.path.join(vault_root, 'second.md'), 'two')\n"
    "    return {'status': 'ok'}\n"
    "def patch(vault_root, *, context=None):\n"
    "    _write(os.path.join(vault_root, 'patched.md'), 'one')\n"
    "    return {'status': 'ok'}\n"
)
# Loads and calls one handler exactly as the upgrader's runner does.
_LOADER = (
    "import sys\n"
    f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
    "import upgrade\n"
    "path, target, vault = sys.argv[1:4]\n"
    "with upgrade._MigrationImportContext(path):\n"
    "    module = upgrade._load_migration_module('9.9.9', path, target)\n"
    "    handler = upgrade._resolve_migration_handler(module, target)\n"
    "    print(handler(vault) if target == 'post_compile' else handler(vault, context={}))\n"
)


def _helper(name: str):
    return runpy.run_path(str(CONTAINER / "killed_upgrade_acceptance.py"), run_name=name)


def _armed_run(tmp_path: Path, kill_key: str, target: str, migration_source: str = _FAKE_MIGRATION):
    helper = _helper("killed_upgrade_prelude_test")
    prelude = tmp_path / "prelude"
    prelude.mkdir()
    (prelude / "sitecustomize.py").write_text(helper["kill_prelude"](kill_key), encoding="utf-8")
    migration = tmp_path / "migrate_to_9_9_9.py"
    migration.write_text(migration_source, encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    completed = subprocess.run(
        [sys.executable, "-c", _LOADER, str(migration), target, str(vault)],
        capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(prelude)},
    )
    return completed, vault, prelude


def test_kill_prelude_exits_after_the_armed_migrations_first_replace(tmp_path):
    completed, vault, prelude = _armed_run(tmp_path, "9.9.9", "post_compile")

    assert completed.returncode == 9, completed.stderr
    assert (vault / "first.md").read_text() == "one", "the first write landed before the kill"
    assert not (vault / "second.md").exists(), "nothing after the first write ran"
    assert json.loads((prelude / "killed.json").read_text()) == {
        "migration": "9.9.9", "path": str(vault / "first.md"),
    }


def test_kill_prelude_arms_the_named_pre_compile_handler(tmp_path):
    completed, vault, prelude = _armed_run(tmp_path, "9.9.9@pre_compile_patch", "pre_compile_patch")

    assert completed.returncode == 9, completed.stderr
    assert (vault / "patched.md").read_text() == "one"
    assert json.loads((prelude / "killed.json").read_text())["migration"] == "9.9.9@pre_compile_patch"


def test_kill_prelude_leaves_other_handlers_and_files_alone(tmp_path):
    completed, vault, _prelude = _armed_run(tmp_path, "9.9.9@pre_compile_patch", "post_compile")

    assert completed.returncode == 0, completed.stderr
    assert (vault / "second.md").read_text() == "two"


def test_kill_prelude_fails_loudly_when_the_armed_handler_never_writes(tmp_path):
    quiet = _FAKE_MIGRATION.replace("def migrate(vault_root):", "def migrate(vault_root):\n    return {'status': 'skipped'}\ndef unused(vault_root):")
    completed, _vault, prelude = _armed_run(tmp_path, "9.9.9", "post_compile", quiet)

    assert completed.returncode not in {0, 9}
    assert "9.9.9 returned without a vault write" in completed.stderr
    assert not (prelude / "killed.json").exists()


def test_kill_points_are_migrations_the_repository_runs_above_the_baseline(tmp_path):
    """The gate dies in real migrations of this source: the 0.68.0 patch DD-085 corrected, and an undeclared post-compile one."""
    helper = _helper("killed_upgrade_kill_points_test")
    historical = runpy.run_path(str(CONTAINER / "historical_upgrade_acceptance.py"), run_name="killed_upgrade_kill_points_historical_test")
    version = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()
    records = historical["expected_migration_records"](REPO_ROOT, version)
    baseline = (0, 53, 5)

    assert helper["PRE_COMPILE_KILL"] == "0.68.0@pre_compile_patch"
    for key in (helper["PRE_COMPILE_KILL"], helper["POST_COMPILE_KILL"]):
        assert key in records
        assert tuple(int(part) for part in key.partition("@")[0].split(".")) > baseline
    post_version = helper["POST_COMPILE_KILL"]
    post = SCRIPTS / "migrations" / ("migrate_to_" + post_version.replace(".", "_") + ".py")
    with upgrade._MigrationImportContext(str(post)):
        module = upgrade._load_migration_module(post_version, str(post), "post_compile")
        assert upgrade._prospective_migration_effects(module, str(tmp_path)) is None, (
            "the post-compile kill exercises the broad rollback scope"
        )
    assert set(helper["POST_KILL_EDITS"]) == {helper["PRE_COMPILE_KILL"], helper["POST_COMPILE_KILL"]}
    for edits in helper["POST_KILL_EDITS"].values():
        assert {kind for _path, kind, _text in edits} == {"append", "create"}


def test_post_kill_edits_must_be_on_journalled_paths(tmp_path, monkeypatch):
    """A fixture that edits outside the journal fails as a fixture error, not as a product recovery failure."""
    from _bootstrap.upgrade_journal import UpgradeJournal

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    helper = _helper("killed_upgrade_journalled_test")
    vault = tmp_path / "vault"
    (vault / "Designs").mkdir(parents=True)
    (vault / "Designs" / "Inherited.md").write_text("original\n")
    (vault / "Loose.md").write_text("loose\n")
    journal = UpgradeJournal.open(str(vault), "1.0.0", "2.0.0")
    journal.capture("pre_compile", [str(vault / "Loose.md")])
    journal.capture_tree("post_compile", str(vault / "Designs"))

    helper["require_journalled"](REPO_ROOT, vault, (
        ("Designs/Inherited.md", "append", "x"), ("Designs/New.md", "create", "x"), ("Loose.md", "append", "x"),
    ))
    with pytest.raises(helper["AcceptanceFailure"], match="fixture error: Other.md is not a journalled file"):
        helper["require_journalled"](REPO_ROOT, vault, (("Other.md", "append", "x"),))
    with pytest.raises(helper["AcceptanceFailure"], match="fixture error: Notes/New.md is under no journalled tree"):
        helper["require_journalled"](REPO_ROOT, vault, (("Notes/New.md", "create", "x"),))


def test_recovery_reading_matches_the_products_recovery_store(tmp_path, monkeypatch):
    """The gate reads what RecoveryStore writes: the manifest's preserved map and byte-identical copies."""
    from _bootstrap.upgrade_journal import RecoveryStore

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    helper = _helper("killed_upgrade_recovery_test")
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Designs" / "Inherited.md"
    note.parent.mkdir()
    note.write_text("original\n", encoding="utf-8")
    edits = helper["edit_after_kill"](vault, (
        ("Designs/Inherited.md", "append", "edited\n"),
        ("Designs/New.md", "create", "new\n"),
    ))
    assert edits == {str(note): b"original\nedited\n", str(vault / "Designs" / "New.md"): b"new\n"}
    half_applied = {str(vault / "Designs" / "Half.md"): b"half applied\n"}
    store = RecoveryStore.for_vault(str(vault))
    for path, content in {**edits, **half_applied}.items():
        store.preserve(path, content)
    store.commit()

    assert helper["require_preserved"](Path(store.directory), REPO_ROOT, {**edits, **half_applied}) == 3
    (Path(store.directory) / store.preserved[str(note)]).write_bytes(b"other\n")
    with pytest.raises(helper["AcceptanceFailure"], match="different bytes"):
        helper["require_preserved"](Path(store.directory), REPO_ROOT, edits)
    with pytest.raises(helper["AcceptanceFailure"], match="did not keep"):
        helper["require_preserved"](Path(store.directory), REPO_ROOT, {str(vault / "Designs" / "Other.md"): b""})

    note.write_text("original\n", encoding="utf-8")
    (vault / "Designs" / "New.md").unlink()
    helper["require_restored"](vault, (("Designs/Inherited.md", "append", "edited\n"), ("Designs/New.md", "create", "new\n")), {"Designs/Inherited.md": b"original\n"})
    note.write_text("original\nedited\n", encoding="utf-8")
    with pytest.raises(helper["AcceptanceFailure"], match="journalled bytes"):
        helper["require_restored"](vault, (("Designs/Inherited.md", "append", "edited\n"),), {"Designs/Inherited.md": b"original\n"})
