#!/usr/bin/env python3
"""Prove in a real container that a killed upgrade is restored and completed by the next run (DD-085).

The same exact historical baseline and fixture as ``historical_upgrade_acceptance``
are upgraded through the production ``brain upgrade``, but the launcher is killed
twice: first after the 0.68.0 pre-compile patch's first vault write, then, on the
rerun that recovers from that kill, after an undeclared post-compile migration's
first artefact write. Each kill leaves no in-process rollback; the next run
restores the vault from the write-ahead journal, keeps the edits made after the
kill in the recovery directory, and the final run completes to the same state the
uninterrupted gate accepts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from historical_upgrade_acceptance import (  # noqa: E402
    AcceptanceFailure,
    _portable_manifest,
    _require_ok_envelope,
    _run,
    _run_json,
    _target_scripts,
    _version_tuple,
    expected_migration_records,
    prepare_acceptance,
    require_upgrade_applied,
    require_upgraded_state,
)


KILL_EXIT_STATUS = 9
RECOVERED_WARNING = "recovered_interrupted_upgrade"
# The kill points, as ledger keys. The 0.68.0 pre-compile patch is the one
# DD-085 corrected and the one that rewrites the shared configuration before
# compile; its local-selection conversion (the path that can grant a control
# command) is not reachable from this baseline, because 0.53.5 predates
# ``defaults.access.initial_profile``, so no local selection can be seeded as
# a real vault of that version would have held one. 0.62.4 is a post-compile
# migration that declares no effects, so it runs under the broad rollback
# scope, and it writes a user artefact (the keyless terminal record).
PRE_COMPILE_KILL = "0.68.0@pre_compile_patch"
POST_COMPILE_KILL = "0.62.4"
# Edits made between each kill and the rerun, every one on a path the journal
# holds at that point: an existing file appended to and a new note created.
# DD-085 restores both to their journalled state and keeps the edited bytes.
POST_KILL_EDITS: dict[str, tuple[tuple[str, str, str], ...]] = {
    PRE_COMPILE_KILL: (
        ("_Config/Memories/README.md", "append", "\nEdited after the pre-compile kill.\n"),
        (
            "_Config/Memories/Post-kill memory.md",
            "create",
            "# Post-kill memory\n\nWritten after the pre-compile kill.\n",
        ),
    ),
    POST_COMPILE_KILL: (
        ("Designs/Inherited.md", "append", "\nEdited after the post-compile kill.\n"),
        (
            "Designs/Post-kill design.md",
            "create",
            "---\ntype: living/design\nstatus: shaping\n---\n\n# Post-kill design\n",
        ),
    ),
}


def kill_prelude(kill_key: str) -> str:
    """The ``sitecustomize`` module that kills the launcher after the named migration's first vault write.

    ``brain`` runs the upgrader inside its own interpreter and appends the
    inherited ``PYTHONPATH``, so a module of this name on it is imported before
    any Brain code and production code gains nothing. The hook watches the
    upgrader load the migration's file for its target and replaces the handler
    with one that lets the first atomic replace land, records the replaced
    path beside this module, and exits without unwinding, as SIGKILL would: no
    rollback runs and no ledger record is written. A handler that returns
    without writing fails the run rather than passing silently.
    """
    version, _, target = kill_key.partition("@")
    target = target or "post_compile"
    filename = "migrate_to_" + version.replace(".", "_") + ".py"
    return (
        "import importlib.util\n"
        "import json\n"
        "import os\n"
        f"KILL = {kill_key!r}\n"
        f"FILENAME = {filename!r}\n"
        f"TARGET = {target!r}\n"
        f"EXIT_STATUS = {KILL_EXIT_STATUS}\n"
        "RECORD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'killed.json')\n"
        "_real_spec = importlib.util.spec_from_file_location\n"
        "\n"
        "\n"
        "def _armed(handler):\n"
        "    def run(*args, **kwargs):\n"
        "        real_replace = os.replace\n"
        "\n"
        "        def replace_then_die(src, dst, *rest, **options):\n"
        "            real_replace(src, dst, *rest, **options)\n"
        "            with open(RECORD, 'w', encoding='utf-8') as handle:\n"
        "                json.dump({'migration': KILL, 'path': os.fspath(dst)}, handle)\n"
        "            os._exit(EXIT_STATUS)\n"
        "\n"
        "        os.replace = replace_then_die\n"
        "        try:\n"
        "            handler(*args, **kwargs)\n"
        "        finally:\n"
        "            os.replace = real_replace\n"
        "        raise RuntimeError(KILL + ' returned without a vault write; the kill point never fired')\n"
        "    return run\n"
        "\n"
        "\n"
        "class _ArmingLoader:\n"
        "    def __init__(self, loader):\n"
        "        self._loader = loader\n"
        "\n"
        "    def create_module(self, spec):\n"
        "        return self._loader.create_module(spec)\n"
        "\n"
        "    def exec_module(self, module):\n"
        "        self._loader.exec_module(module)\n"
        "        name = 'migrate' if TARGET == 'post_compile' else module.TARGET_HANDLERS[TARGET]\n"
        "        setattr(module, name, _armed(getattr(module, name)))\n"
        "\n"
        "\n"
        "def _spec_from_file_location(name, location=None, *args, **kwargs):\n"
        "    spec = _real_spec(name, location, *args, **kwargs)\n"
        "    if (\n"
        "        spec is not None and spec.loader is not None and location is not None\n"
        "        and os.path.basename(os.fspath(location)) == FILENAME\n"
        "    ):\n"
        "        spec.loader = _ArmingLoader(spec.loader)\n"
        "    return spec\n"
        "\n"
        "\n"
        "importlib.util.spec_from_file_location = _spec_from_file_location\n"
    )


def _journal_module(target_source: Path):
    """The target source's own journal rules: location, header, stages and the recovery store layout."""
    _target_scripts(target_source)
    from _bootstrap import upgrade_journal

    return upgrade_journal


def _expected_state_home() -> Path:
    """Where DD-085 puts machine-local state: an absolute ``$XDG_STATE_HOME``, else ``~/.local/state``."""
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    return Path(os.environ.get("HOME") or Path.home()) / ".local" / "state"


def _installed_version(vault: Path) -> str:
    return (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8").strip()


def _recorded_migrations(vault: Path) -> set[str]:
    try:
        ledger = json.loads(
            (vault / ".brain" / "local" / "migrations.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise AcceptanceFailure(f"migration ledger is unreadable: {exc}") from exc
    recorded = ledger.get("migrations") if isinstance(ledger, dict) else None
    if not isinstance(recorded, dict):
        raise AcceptanceFailure("migration ledger is invalid")
    return set(recorded)


def _upgrade_argv(vault: Path, upgrade_request: str, *extra: str) -> list[str]:
    return ["brain", "--vault", str(vault), "upgrade", "--request-json", upgrade_request, *extra, "--json"]


def _has_recovered_warning(envelope: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and RECOVERED_WARNING in str(item.get("message", ""))
        for item in envelope.get("warnings", [])
    )


def kill_upgrade(
    vault: Path,
    kill_key: str,
    upgrade_request: str,
    commands: list[dict[str, Any]],
) -> dict[str, str]:
    """Run the production upgrade with the kill prelude on its PYTHONPATH; return what the kill recorded."""
    prelude_dir = Path(tempfile.mkdtemp(prefix=f"brain-lab-kill-{kill_key.replace('@', '-')}-"))
    (prelude_dir / "sitecustomize.py").write_text(kill_prelude(kill_key), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(prelude_dir), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    completed, receipt = _run(
        _upgrade_argv(vault, upgrade_request),
        cwd=vault,
        accepted=frozenset(range(256)),
        env=env,
    )
    commands.append(receipt)
    if completed.returncode != KILL_EXIT_STATUS:
        detail = completed.stderr.strip()[-2000:] or completed.stdout.strip()[-2000:]
        raise AcceptanceFailure(
            f"{kill_key}: brain upgrade exited {completed.returncode} instead of the kill "
            f"status {KILL_EXIT_STATUS}: {detail}"
        )
    try:
        record = json.loads((prelude_dir / "killed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AcceptanceFailure(f"{kill_key}: the kill left no record of its write: {exc}") from exc
    if record.get("migration") != kill_key or not isinstance(record.get("path"), str):
        raise AcceptanceFailure(f"{kill_key}: the kill record is not this migration's: {record}")
    return {"migration": kill_key, "path": record["path"], "prelude": str(prelude_dir)}


def require_killed_state(
    vault: Path,
    target_source: Path,
    record: dict[str, str],
    *,
    historical_version: str,
    target_core: str,
    stage: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """VERSION is untouched, the journal is where DD-085 puts it, and the killed write is half applied.

    The journal's own capture is the witness that the kill was mid-migration:
    it holds the path's original bytes, or absence, and the vault now holds
    something else, while the ledger has no record of the migration. Returns
    the record and the half-applied bytes, which the restore must keep.
    """
    journal_module = _journal_module(target_source)
    if _installed_version(vault) != historical_version:
        raise AcceptanceFailure(f"{record['migration']}: the kill changed VERSION")
    journal_dir = Path(journal_module.journal_directory(str(vault)))
    if journal_dir.parent != _expected_state_home() / "brain" / "upgrade-journals":
        raise AcceptanceFailure(f"journal is not under the machine state home: {journal_dir}")
    try:
        journal = journal_module.UpgradeJournal.load(str(vault))
    except journal_module.UpgradeJournalUnreadable as exc:
        raise AcceptanceFailure(f"{record['migration']}: the kill left an unreadable journal: {exc}") from exc
    if journal is None:
        raise AcceptanceFailure(f"{record['migration']}: the kill left no journal at {journal_dir}")
    if (journal.old_version, journal.new_version) != (historical_version, target_core):
        raise AcceptanceFailure(
            f"journal names the wrong run: {journal.old_version} → {journal.new_version}"
        )
    killed_path = Path(record["path"])
    try:
        relative = killed_path.relative_to(vault).as_posix()
    except ValueError as exc:
        raise AcceptanceFailure(f"{record['migration']}: the kill wrote outside the vault: {killed_path}") from exc
    original = journal.path_state(stage, str(killed_path))
    if original is None:
        raise AcceptanceFailure(
            f"{record['migration']}: the killed write to {relative} was not journalled in the {stage} stage"
        )
    if not killed_path.is_file():
        raise AcceptanceFailure(f"{record['migration']}: the killed write left no file at {relative}")
    current = killed_path.read_bytes()
    journalled = original["content"] if original["exists"] else None
    if current == journalled:
        raise AcceptanceFailure(f"{record['migration']}: {relative} still holds its journalled bytes; nothing was half applied")
    if record["migration"] in _recorded_migrations(vault):
        raise AcceptanceFailure(f"{record['migration']}: the killed migration was recorded in the ledger")
    return {
        "migration": record["migration"],
        "killed_write": relative,
        "half_applied_sha256": hashlib.sha256(current).hexdigest(),
        "journal": journal.directory,
        "journal_started_at": journal.header.get("started_at"),
        "prelude": record["prelude"],
    }, {str(killed_path): current}


def require_journalled(
    target_source: Path, vault: Path, edits: tuple[tuple[str, str, str], ...],
) -> None:
    """A fixture check: each post-kill edit is on a path the leftover journal holds in some stage."""
    journal_module = _journal_module(target_source)
    journal = journal_module.UpgradeJournal.load(str(vault))
    if journal is None:
        raise AcceptanceFailure("fixture error: no journal to edit against")
    files: set[str] = set()
    roots: set[str] = set()
    for stage in journal_module.STAGES:
        stage_files, stage_roots = journal.stage_entries(stage)
        files.update(stage_files)
        roots.update(stage_roots)
    for relative, kind, _text in edits:
        path = str(vault / relative)
        if kind == "append" and path not in files:
            raise AcceptanceFailure(f"fixture error: {relative} is not a journalled file, so its edit would not be restored")
        if kind == "create" and not any(path.startswith(root + os.sep) for root in roots):
            raise AcceptanceFailure(f"fixture error: {relative} is under no journalled tree, so it would not be removed")


def _tree_digest(root: Path) -> str:
    """One digest of every file name and content under ``root``, so a dry run can be shown to change nothing there."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def require_pending_restore_preview(
    vault: Path,
    upgrade_request: str,
    commands: list[dict[str, Any]],
    journal_dir: Path,
    *,
    historical_version: str,
    target_core: str,
    must_list: set[str],
) -> None:
    """A dry run with a leftover journal reports the pending restore, previews what the restored ledger implies, and writes nothing."""
    manifest_before = _portable_manifest(vault)
    journal_before = _tree_digest(journal_dir)
    preview, receipt = _run_json(_upgrade_argv(vault, upgrade_request, "--dry-run"), cwd=vault)
    commands.append(receipt)
    preview_result = _require_ok_envelope(preview, "brain.upgrade dry run")
    if preview_result.get("status") != "planned" or preview.get("committed_effects") != []:
        raise AcceptanceFailure("brain.upgrade dry run after the kill did not plan without effects")
    if (preview_result.get("old_version"), preview_result.get("new_version")) != (historical_version, target_core):
        raise AcceptanceFailure("brain.upgrade dry run after the kill selected the wrong versions")
    if not _has_recovered_warning(preview):
        raise AcceptanceFailure("brain.upgrade dry run did not report the pending journal restore")
    migrations = preview_result.get("migrations")
    if not isinstance(migrations, list) or not must_list <= set(migrations):
        raise AcceptanceFailure(
            f"brain.upgrade dry run did not preview the migrations the restored ledger implies: {migrations}"
        )
    if _portable_manifest(vault) != manifest_before:
        raise AcceptanceFailure("brain.upgrade dry run changed portable vault state after the kill")
    if _tree_digest(journal_dir) != journal_before:
        raise AcceptanceFailure("brain.upgrade dry run changed the journal")


def edit_after_kill(vault: Path, edits: tuple[tuple[str, str, str], ...]) -> dict[str, bytes]:
    """Apply the post-kill edits; return the bytes each path then holds, keyed by absolute path."""
    written = {}
    for relative, kind, text in edits:
        path = vault / relative
        if kind == "append":
            content = path.read_bytes() + text.encode("utf-8")
        elif kind == "create":
            if path.exists():
                raise AcceptanceFailure(f"post-kill note already exists: {relative}")
            content = text.encode("utf-8")
        else:
            raise AcceptanceFailure(f"unknown post-kill edit kind {kind!r}")
        path.write_bytes(content)
        written[str(path)] = content
    return written


def require_preserved(
    recovery_dir: Path,
    target_source: Path,
    edits: dict[str, bytes],
) -> int:
    """Every given path, post-kill edit or half-applied write, is in the recovery directory byte for byte; returns how many paths it kept."""
    journal_module = _journal_module(target_source)
    try:
        manifest = json.loads((recovery_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AcceptanceFailure(f"recovery manifest is unreadable: {recovery_dir}: {exc}") from exc
    if manifest.get("schema") != journal_module.RECOVERY_SCHEMA:
        raise AcceptanceFailure(f"recovery manifest has the wrong schema: {manifest.get('schema')}")
    preserved = manifest.get("preserved")
    if not isinstance(preserved, dict):
        raise AcceptanceFailure("recovery manifest has no preserved map")
    for path, content in edits.items():
        copy = preserved.get(path)
        if not isinstance(copy, str):
            raise AcceptanceFailure(f"recovery did not keep the changed bytes of {path}")
        if (recovery_dir / copy).read_bytes() != content:
            raise AcceptanceFailure(f"recovery kept different bytes for {path}")
    return len(preserved)


def require_restored(vault: Path, edits: tuple[tuple[str, str, str], ...], originals: dict[str, bytes]) -> None:
    """The edited file holds its journalled bytes again and the created note is gone from the vault."""
    for relative, kind, _text in edits:
        path = vault / relative
        if kind == "create":
            if path.exists():
                raise AcceptanceFailure(f"restore left the post-kill note in the vault: {relative}")
        elif path.read_bytes() != originals[relative]:
            raise AcceptanceFailure(f"restore did not return {relative} to its journalled bytes")


def _only_recovery_dir(recovery_root: Path) -> Path:
    found = [path for path in recovery_root.iterdir() if path.is_dir()] if recovery_root.is_dir() else []
    if len(found) != 1:
        raise AcceptanceFailure(f"expected one recovery directory under {recovery_root}, found {len(found)}")
    return found[0]


def run_acceptance(vault: Path, target_source: Path, historical_version: str) -> dict[str, Any]:
    vault = vault.resolve()
    target_source = target_source.resolve()
    journal_module = _journal_module(target_source)
    journal_dir = Path(journal_module.journal_directory(str(vault)))
    recovery_root = Path(journal_module.recovery_root(str(vault)))
    if journal_dir.exists() or recovery_root.exists():
        raise AcceptanceFailure("the baseline already holds upgrade journal or recovery state")
    prepared = prepare_acceptance(vault, target_source, historical_version)
    target_core = prepared.target_core
    upgrade_request = prepared.upgrade_request
    commands = prepared.commands
    pending = expected_migration_records(target_source, target_core)
    for kill_key in (PRE_COMPILE_KILL, POST_COMPILE_KILL):
        version = _version_tuple(kill_key.partition("@")[0])
        if kill_key not in pending or version <= _version_tuple(historical_version):
            raise AcceptanceFailure(f"kill point {kill_key} is not a migration this upgrade runs")
    originals = {
        relative: (vault / relative).read_bytes()
        for edits in POST_KILL_EDITS.values()
        for relative, kind, _text in edits
        if kind == "append"
    }

    # --- first kill: the 0.68.0 pre-compile patch, after its first write ---
    first, first_half_applied = require_killed_state(
        vault, target_source,
        kill_upgrade(vault, PRE_COMPILE_KILL, upgrade_request, commands),
        historical_version=historical_version, target_core=target_core,
        stage=journal_module.STAGE_PRE_COMPILE,
    )
    require_journalled(target_source, vault, POST_KILL_EDITS[PRE_COMPILE_KILL])
    first_edits = edit_after_kill(vault, POST_KILL_EDITS[PRE_COMPILE_KILL])
    require_pending_restore_preview(
        vault, upgrade_request, commands, journal_dir,
        historical_version=historical_version, target_core=target_core,
        must_list={PRE_COMPILE_KILL, POST_COMPILE_KILL},
    )

    # --- second kill: the rerun restores the first journal, then dies in 0.62.4 ---
    second, second_half_applied = require_killed_state(
        vault, target_source,
        kill_upgrade(vault, POST_COMPILE_KILL, upgrade_request, commands),
        historical_version=historical_version, target_core=target_core,
        stage=journal_module.STAGE_POST_COMPILE,
    )
    if not second["journal_started_at"] > first["journal_started_at"]:
        raise AcceptanceFailure("the rerun did not open a new journal after restoring the first")
    recorded = _recorded_migrations(vault)
    if PRE_COMPILE_KILL not in recorded:
        raise AcceptanceFailure("the rerun did not re-run and record the first killed migration")
    first_recovery = _only_recovery_dir(recovery_root)
    first_kept = require_preserved(first_recovery, target_source, {**first_edits, **first_half_applied})
    require_restored(vault, POST_KILL_EDITS[PRE_COMPILE_KILL], originals)
    require_journalled(target_source, vault, POST_KILL_EDITS[POST_COMPILE_KILL])
    second_edits = edit_after_kill(vault, POST_KILL_EDITS[POST_COMPILE_KILL])
    # The on-disk ledger now records the first kill point; the preview must
    # still list it, because the restore of the second journal rewinds it.
    require_pending_restore_preview(
        vault, upgrade_request, commands, journal_dir,
        historical_version=historical_version, target_core=target_core,
        must_list={PRE_COMPILE_KILL, POST_COMPILE_KILL},
    )

    # --- the final run restores the second journal and completes ---
    applied, receipt = _run_json(_upgrade_argv(vault, upgrade_request), cwd=vault, timeout=1800)
    commands.append(receipt)
    require_upgrade_applied(applied, vault, target_core)
    if not _has_recovered_warning(applied):
        raise AcceptanceFailure("brain.upgrade did not report that it recovered the interrupted upgrade")
    upgrade_log = json.loads(
        (vault / ".brain" / "local" / "last-upgrade.json").read_text(encoding="utf-8")
    )
    recovery = upgrade_log.get("recovery")
    warning = next(
        (item for item in upgrade_log.get("warnings", []) if item.get("code") == RECOVERED_WARNING),
        None,
    )
    if not isinstance(recovery, dict) or recovery.get("action") != "restored" or warning is None:
        raise AcceptanceFailure("the upgrade log does not record the journal restore")
    if warning.get("interrupted_stage") != "post_compile_migrations":
        raise AcceptanceFailure(f"recovery named the wrong interrupted stage: {warning.get('interrupted_stage')}")
    second_recovery = Path(str(recovery.get("recovery_directory") or ""))
    if second_recovery.parent != recovery_root or second_recovery == first_recovery:
        raise AcceptanceFailure(f"recovery did not name a new recovery directory: {second_recovery}")
    if warning.get("recovery_directory") != str(second_recovery):
        raise AcceptanceFailure("the recovery warning does not name the recovery directory")
    second_kept = require_preserved(second_recovery, target_source, {**second_edits, **second_half_applied})
    if recovery.get("preserved_paths") != second_kept:
        raise AcceptanceFailure("the recovery report miscounts the paths it kept")
    require_restored(vault, POST_KILL_EDITS[POST_COMPILE_KILL], originals)
    if journal_dir.exists():
        raise AcceptanceFailure("the committed upgrade left its journal behind")

    # The vault and machine are now what the uninterrupted gate accepts.
    return {
        "schema": "brain-lab.killed-upgrade-acceptance/1",
        "kills": [first, second],
        "preview_after_each_kill_reported_pending_restore": True,
        "recovery": {
            "directories": [str(first_recovery), str(second_recovery)],
            "preserved_paths": [first_kept, second_kept],
            "half_applied_writes_preserved": True,
            "post_kill_edits_preserved": True,
            "post_kill_edits_restored": True,
            "interrupted_stage": warning["interrupted_stage"],
            "journal_discarded_after_commit": True,
        },
        **require_upgraded_state(vault, target_source, historical_version, prepared),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--target-source", required=True, type=Path)
    parser.add_argument("--historical-version", required=True)
    args = parser.parse_args()
    try:
        result = run_acceptance(args.vault, args.target_source, args.historical_version)
    except (AcceptanceFailure, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
