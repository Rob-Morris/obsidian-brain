"""VERSION is the upgrade commit witness; force never replays migrations (DD-084).

An in-process matrix through ``upgrade()``: the content guard, the
same-version guard, the deferred VERSION write, resumption of interrupted
runs, ledger-first rollback, the persisted template capture and the
post-commit outcomes. Each case that has a preview asserts dry-run parity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

import upgrade
from test_upgrade_migrations import _REAL_SCRIPTS, _counter_migration, _make_source, _make_vault


@pytest.fixture(autouse=True)
def _close_machine_state(tmp_path, monkeypatch, fake_home):
    """Keep the matrix off the developer's CLI, runtime, MCP state and client skill markers."""
    from _bootstrap import mcp_transport

    monkeypatch.setattr(mcp_transport, "delegate_machine_command", lambda *args, **kwargs: {"status": "ok", "committed_effects": []})
    monkeypatch.setattr(
        upgrade,
        "CLI_TARGET_LOCATIONS",
        (tmp_path / "machine" / "user" / "bin" / "brain", tmp_path / "machine" / "system" / "bin" / "brain"),
    )
    monkeypatch.setattr(
        upgrade,
        "_ensure_central_runtime",
        lambda _vault, *, requirements_changed, sync_deps: {
            "outcome": upgrade.RUNTIME_REUSED,
            "requirements_changed": requirements_changed,
        },
    )
    monkeypatch.setattr(upgrade, "_complete_runtime_readiness", lambda _vault: {"outcome": "ok", "message": "ready"})
    monkeypatch.setattr(upgrade, "_inspect_runtime_orphans", lambda _vault: {"outcome": "ok", "orphan_candidates": 0, "message": "tidy"})


def _raising_migration(message: str) -> str:
    return f"def migrate(vault_root):\n    raise RuntimeError({message!r})\n"


def _install_core(vault: Path, source: Path) -> None:
    """Make the installed core match the source exactly, VERSION included."""
    shutil.rmtree(vault / ".brain-core")
    shutil.copytree(source, vault / ".brain-core")


def _crash_state(vault: Path, source: Path, old_version: str) -> None:
    """Leave the vault as a kill after the copy would: new core files, old VERSION."""
    _install_core(vault, source)
    (vault / ".brain-core" / "VERSION").write_text(old_version + "\n")


def _write_ledger(vault: Path, entries: dict) -> None:
    path = vault / ".brain" / "local" / "migrations.json"
    path.write_text(json.dumps({"schema_version": 1, "migrations": entries}, indent=2) + "\n")


def _ledger_keys(vault: Path) -> set[str]:
    path = vault / ".brain" / "local" / "migrations.json"
    if not path.exists():
        return set()
    return set(json.loads(path.read_text())["migrations"])


def _counter(vault: Path, name: str) -> int:
    path = vault / ".brain" / "local" / name
    return int(path.read_text()) if path.exists() else 0


def _version(vault: Path) -> str:
    return (vault / ".brain-core" / "VERSION").read_text().strip()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file but lock endpoints: a refused run holds the vault lock while it decides."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.suffix != ".lock"
    }


def _preview(vault: Path, source: Path, **kwargs) -> dict:
    return upgrade.upgrade(str(vault), str(source), dry_run=True, sync=False, **kwargs)


def _previewed_versions(result: dict) -> list[str]:
    return [item["version"] for item in result.get("migrations_preview", [])]


def _warning_codes(result: dict) -> set[str]:
    return {w["code"] for w in result.get("warnings", []) if "code" in w}


# --- 1. force on a complete ledger -------------------------------------------

def test_forced_same_version_run_on_a_complete_ledger_runs_nothing(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_1_0_0.py": _raising_migration("historical replay"),
        "migrate_to_2_0_0.py": _raising_migration("current replay"),
    })
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    _write_ledger(vault, {"1.0.0": {"status": "ok"}, "2.0.0": {"status": "ok"}})

    preview = _preview(vault, source, force=True)
    result = upgrade.upgrade(str(vault), str(source), force=True, sync=False)

    assert preview["status"] == "ok" and _previewed_versions(preview) == []
    assert result["status"] == "ok", result
    assert "migrations" not in result
    assert _ledger_keys(vault) == {"1.0.0", "2.0.0"}
    assert _version(vault) == "2.0.0"


# --- 2. content guard ---------------------------------------------------------

def _content_ahead_cases():
    return [
        pytest.param("2.0.0", "1.0.0", {}, "2.0.0", id="downgrade"),
        pytest.param("1.0.0", "2.0.0", {"3.0.0": {"status": "ok"}}, "3.0.0", id="source-between-version-and-ledger"),
        pytest.param("2.0.0", "2.0.0", {"3.0.0": {"status": "ok"}}, "3.0.0", id="same-version-ledger-ahead"),
    ]


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize(("installed", "source_version", "ledger", "content"), _content_ahead_cases())
def test_content_guard_refuses_an_older_source_with_no_effect(tmp_path, installed, source_version, ledger, content, force):
    source = _make_source(tmp_path, source_version, migrations={"migrate_to_1_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, installed)
    if ledger:
        _write_ledger(vault, ledger)
    before = _tree_bytes(vault)

    result = upgrade.upgrade(str(vault), str(source), force=force, sync=False)
    preview = _preview(vault, source, force=force)

    assert result["status"] == "error"
    assert result["reason"] == "content_ahead"
    assert result["rollback_verified"] is True
    assert preview == result
    assert _tree_bytes(vault) == before
    message = result["message"]
    assert f"content is at {content}" in message
    assert "Migrations only run forward" in message
    assert "install.sh" in message and "upgrade.py --source" in message
    assert "brain upgrade" not in message
    if ledger:
        assert "records 3.0.0 above this source" in message


@pytest.mark.parametrize("json_output", [False, True])
def test_content_guard_refusal_exits_one_from_main(tmp_path, monkeypatch, capsys, json_output):
    source = _make_source(tmp_path, "1.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    argv = ["upgrade.py", "--source", str(source), "--vault", str(vault), "--force"]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))

    with pytest.raises(SystemExit) as exc:
        upgrade.main()

    assert exc.value.code == 1
    out, err = capsys.readouterr()
    if json_output:
        assert json.loads(out)["reason"] == "content_ahead"
    else:
        assert "Upgrade refused" in err
    assert _version(vault) == "2.0.0"


def test_content_guard_ignores_keys_that_do_not_parse_and_counts_keys_without_a_version_field(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    tolerant = _make_vault(tmp_path, "1.0.0")
    _write_ledger(tolerant, {"garbage": {"status": "ok"}, "9.9": {"status": "ok"}, "1.0.0-rc1": {}})
    (tmp_path / "strict").mkdir()
    strict = _make_vault(tmp_path / "strict", "1.0.0")
    _write_ledger(strict, {"3.0.0@pre_compile_patch": {"status": "ok"}})

    assert upgrade.upgrade(str(tolerant), str(source), sync=False)["status"] == "ok"
    refused = upgrade.upgrade(str(strict), str(source), sync=False)
    assert refused["reason"] == "content_ahead"
    assert "records 3.0.0 above this source" in refused["message"]


# --- 3. VERSION stays old through every failure before the commit --------------

def _skill_failure(monkeypatch):
    import _skill_library

    def fail(_vault_root, **_kwargs):
        raise RuntimeError("skill reconciliation boom")

    monkeypatch.setattr(_skill_library, "reconcile_core_overrides", fail)


_OBSERVING_RAISE = (
    "import os\n"
    "def {name}(vault_root, *args, **kwargs):\n"
    "    with open(os.path.join(vault_root, '.brain-core', 'VERSION')) as handle:\n"
    "        raise RuntimeError('{label} boom; observed ' + handle.read().strip())\n"
)


@pytest.mark.parametrize("site", ["precompile", "compile", "postcompile", "skills", "cutover"])
def test_version_stays_old_through_every_failure_up_to_the_cutover(tmp_path, monkeypatch, site):
    migrations = {"migrate_to_2_0_0.py": _counter_migration("count.txt")}
    if site == "precompile":
        migrations["migrate_to_1_5_0.py"] = (
            "TARGET_HANDLERS = {'pre_compile_patch': 'patch'}\n"
            + _OBSERVING_RAISE.format(name="patch", label="patch")
        )
    if site == "postcompile":
        migrations["migrate_to_2_0_0.py"] = _OBSERVING_RAISE.format(name="migrate", label="migration")
    source = _make_source(tmp_path, "2.0.0", migrations=migrations)
    vault = _make_vault(tmp_path, "1.0.0")
    before = _tree_bytes(vault / ".brain-core")
    observed = []

    def failing_compile(_vault_root):
        observed.append(_version(vault))
        return "compile boom"

    if site == "compile":
        monkeypatch.setattr(upgrade, "_validate_compile", failing_compile)
    if site == "skills":
        _skill_failure(monkeypatch)

    def cutover(_result):
        observed.append(_version(vault))
        raise RuntimeError("cutover boom")

    result = upgrade.upgrade(
        str(vault), str(source), sync=False,
        commit_callback=cutover if site == "cutover" else None,
    )

    assert result["status"] == "error", result
    assert result["rollback_verified"] is True
    assert observed == (["1.0.0"] if site in {"cutover", "compile"} else [])
    if site in {"precompile", "postcompile"}:
        assert "observed 1.0.0" in result["message"]
    assert _tree_bytes(vault / ".brain-core") == before


# --- 4. a simulated crash resumes (old, new] minus recorded ----------------------

def test_a_crash_after_the_copy_resumes_only_the_unrecorded_migrations(tmp_path):
    source = _make_source(tmp_path, "3.0.0", migrations={
        "migrate_to_2_0_0.py": _counter_migration("a.txt"),
        "migrate_to_2_5_0.py": _counter_migration("b.txt"),
        "migrate_to_3_0_0.py": _counter_migration("c.txt"),
    })
    vault = _make_vault(tmp_path, "1.0.0")
    _crash_state(vault, source, "1.0.0")
    _write_ledger(vault, {"2.0.0": {"status": "ok"}})
    (vault / ".brain" / "local" / "a.txt").write_text("1")

    preview = _preview(vault, source)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert _previewed_versions(preview) == ["2.5.0", "3.0.0"]
    assert result["status"] == "ok", result
    assert [item["version"] for item in result["migrations"]] == ["2.5.0", "3.0.0"]
    assert (_counter(vault, "a.txt"), _counter(vault, "b.txt"), _counter(vault, "c.txt")) == (1, 1, 1)
    assert _version(vault) == "3.0.0"

    later = _make_source(tmp_path, "4.0.0", migrations={
        "migrate_to_2_0_0.py": _counter_migration("a.txt"),
        "migrate_to_2_5_0.py": _counter_migration("b.txt"),
        "migrate_to_3_0_0.py": _counter_migration("c.txt"),
        "migrate_to_3_5_0.py": _counter_migration("d.txt"),
        "migrate_to_4_0_0.py": _counter_migration("e.txt"),
    })
    _crash_state(vault, later, "3.0.0")
    _write_ledger(vault, {**{k: {"status": "ok"} for k in _ledger_keys(vault)}, "3.5.0": {"status": "ok"}})
    (vault / ".brain" / "local" / "d.txt").write_text("1")

    second_preview = _preview(vault, later)
    second = upgrade.upgrade(str(vault), str(later), sync=False)

    assert _previewed_versions(second_preview) == ["4.0.0"]
    assert [item["version"] for item in second["migrations"]] == ["4.0.0"]
    assert [_counter(vault, name) for name in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt")] == [1, 1, 1, 1, 1]
    assert _version(vault) == "4.0.0"


# --- 5. B1: the persisted template capture ---------------------------------------

def _authorisation_fixture(tmp_path):
    from _common._yaml import dump_mapping_text

    migration = (_REAL_SCRIPTS / "migrations" / "migrate_to_0_68_0.py").read_text()
    source = _make_source(tmp_path, "0.68.0", migrations={"migrate_to_0_68_0.py": migration})
    vault = _make_vault(tmp_path, "0.67.3")
    old = {"vault": {"profiles": {"reader": {"allow": ["vault.read-file"]}}},
           "defaults": {"access": {"initial_profile": "reader"}}}
    new = {"vault": {"profiles": {"reader": {"allow": ["access.prepare", "vault.read-file"]}}},
           "defaults": {"access": {"initial": {"mode": "normal"}}}}
    for directory, value in ((vault / ".brain-core" / "defaults", old), (source / "defaults", new)):
        directory.mkdir()
        (directory / "config.yaml").write_text(dump_mapping_text(value))
    shutil.copy2(_REAL_SCRIPTS.parent / "defaults" / "command-authority.json", source / "defaults" / "command-authority.json")
    (vault / ".brain" / "config.yaml").write_text(dump_mapping_text({"defaults": {"access": {"initial_profile": "reader"}}}))
    return source, vault


def _converted_initial(vault):
    from _common._yaml import load_mapping_file

    return load_mapping_file(vault / ".brain" / "config.yaml")["defaults"]["access"]


def test_a_resumed_run_converts_legacy_grants_against_the_persisted_old_template(tmp_path):
    from _command_interface.authorisation_migration import TEMPLATE_CAPTURE_PATH, load_or_persist_template_capture

    source, vault = _authorisation_fixture(tmp_path)
    load_or_persist_template_capture(str(vault), "0.67.3")
    _crash_state(vault, source, "0.67.3")
    assert "access.prepare" in (vault / ".brain-core" / "defaults" / "config.yaml").read_text()

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert _converted_initial(vault) == {"initial": {"mode": "explicit", "commands": ["vault.read-file"]}}
    assert not (vault / TEMPLATE_CAPTURE_PATH).exists(), "the capture is removed at the commit"
    assert _version(vault) == "0.68.0"


def test_a_capture_for_another_version_is_ignored_and_rewritten(tmp_path):
    from _command_interface.authorisation_migration import TEMPLATE_CAPTURE_PATH, TEMPLATE_CAPTURE_SCHEMA

    source, vault = _authorisation_fixture(tmp_path)
    (vault / TEMPLATE_CAPTURE_PATH).write_text(json.dumps({
        "schema": TEMPLATE_CAPTURE_SCHEMA, "version": "0.60.0", "template_revision": None,
        "template_layer": {"vault": {"profiles": {"reader": {"allow": ["bogus"]}}}, "defaults": {}},
    }))

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert _converted_initial(vault) == {"initial": {"mode": "explicit", "commands": ["vault.read-file"]}}
    assert not (vault / TEMPLATE_CAPTURE_PATH).exists()


# --- 6. B2: the ledger is restored before the content ---------------------------

class _Killed(BaseException):
    pass


def test_an_interrupted_rollback_has_already_unrecorded_the_migrations(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_1_5_0.py": _counter_migration("first.txt"),
        "migrate_to_2_0_0.py": _raising_migration("second boom"),
    })
    vault = _make_vault(tmp_path, "1.0.0")
    from _bootstrap import upgrade_journal

    real_restore = upgrade_journal.restore_snapshots

    def kill_during_content_restore(snapshots, **kwargs):
        # The ledger-first pass carries no roots; the content passes do.
        if kwargs.get("roots"):
            raise _Killed()
        return real_restore(snapshots, **kwargs)

    monkeypatch.setattr(upgrade_journal, "restore_snapshots", kill_during_content_restore)
    with pytest.raises(_Killed):
        upgrade.upgrade(str(vault), str(source), sync=False)

    assert _ledger_keys(vault) == set(), "the ledger is restored first"
    assert _counter(vault, "first.txt") == 1, "the content restore had not reached the migration's effect"
    assert _version(vault) == "1.0.0"

    monkeypatch.setattr(upgrade_journal, "restore_snapshots", real_restore)
    (source / "scripts" / "migrations" / "migrate_to_2_0_0.py").write_text(_counter_migration("second.txt"))
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert "recovered_interrupted_upgrade" in _warning_codes(result), "the journal of the killed rollback is restored first"
    assert [item["version"] for item in result["migrations"]] == ["1.5.0", "2.0.0"]
    assert _counter(vault, "first.txt") == 1, "the migration reruns from the restored pre-run state"
    assert _counter(vault, "second.txt") == 1
    assert _version(vault) == "2.0.0"


# --- 7. post-commit outcomes -----------------------------------------------------

def test_a_version_commit_failure_is_partial_and_the_next_run_finishes_it(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    backups = []
    real_backup = upgrade._backup_brain_core
    monkeypatch.setattr(upgrade, "_backup_brain_core", lambda target: backups.append(real_backup(target)) or backups[-1])
    real_commit = upgrade._commit_version

    def fail_commit(_target, _source):
        raise OSError("disk full")

    monkeypatch.setattr(upgrade, "_commit_version", fail_commit)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "partial"
    assert result["version_commit"]["outcome"] == "error"
    assert "disk full" in result["version_commit"]["message"]
    assert "rerun the same upgrade" in result["message"]
    assert "router_compile" not in result and "central_runtime" not in result, "post-commit stages are skipped"
    assert not Path(backups[0]).exists(), "the backup is released"
    assert _version(vault) == "1.0.0"
    assert _counter(vault, "count.txt") == 1
    from _command_interface.authorisation_migration import TEMPLATE_CAPTURE_PATH
    assert (vault / TEMPLATE_CAPTURE_PATH).is_file(), "the capture outlives a failed commit"

    monkeypatch.setattr(upgrade, "_commit_version", real_commit)
    again = upgrade.upgrade(str(vault), str(source), sync=False)

    assert again["status"] == "ok", again
    assert not (vault / TEMPLATE_CAPTURE_PATH).exists()
    assert "migrations" not in again
    assert _counter(vault, "count.txt") == 1
    assert _version(vault) == "2.0.0"
    assert again["router_compile"] == {"outcome": "ok"}


def test_a_version_commit_failure_exits_one_from_main(tmp_path, monkeypatch, capsys):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")

    def fail_commit(_target, _source):
        raise OSError("disk full")

    monkeypatch.setattr(upgrade, "_commit_version", fail_commit)
    monkeypatch.setattr(sys, "argv", ["upgrade.py", "--source", str(source), "--vault", str(vault), "--no-sync"])

    with pytest.raises(SystemExit) as exc:
        upgrade.main()

    assert exc.value.code == 1
    assert "VERSION was not committed" in capsys.readouterr().err


def test_a_post_commit_compile_failure_is_partial_with_version_committed(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    monkeypatch.setattr(
        upgrade, "_validate_compile",
        lambda vault_root: "custom taxonomy boom" if _version(Path(vault_root)) == "2.0.0" else None,
    )

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "partial"
    assert result["router_compile"] == {
        "outcome": "error",
        "message": "Router recompilation failed after the commit: custom taxonomy boom",
    }
    assert "router recompilation requires recovery" in result["message"]
    assert _version(vault) == "2.0.0"


# --- 8. the router is fresh after an ordinary upgrade ----------------------------

def test_the_router_is_fresh_and_stamped_with_the_target_after_an_upgrade(tmp_path):
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    from test_upgrade import _make_minimal_upgrade_vault, _make_real_compile_source

    source = _make_real_compile_source(tmp_path, version="0.29.1")
    vault = _make_minimal_upgrade_vault(tmp_path, version="0.28.7")

    result = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=False)

    assert result["status"] == "ok", result
    assert result["router_compile"] == {"outcome": "ok"}
    router = require_fresh_compiled_router(vault)
    assert router["meta"]["brain_core_version"] == "0.29.1"


# --- 9. the same-version guard -------------------------------------------------------

def test_a_mismatched_same_version_core_re_applies_without_force(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "2.0.0")

    preview = _preview(vault, source)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    for outcome in (preview, result):
        assert outcome["status"] == "ok"
        assert "core_mismatch" in _warning_codes(outcome)
        assert _previewed_versions(outcome) == []
    assert "migrations" not in result
    assert (vault / ".brain-core" / "scripts" / "migrations" / "migrate_to_2_0_0.py").is_file()
    assert _counter(vault, "count.txt") == 0


def test_a_skipped_run_never_prepares_the_cutover(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)

    def refuse():
        raise AssertionError("a run that will be skipped must not run the cutover preflight")

    for dry_run in (True, False):
        result = upgrade.upgrade(str(vault), str(source), dry_run=dry_run, sync=False, prepare_cutover=refuse)
        assert result["status"] == "skipped", result


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_refused_cutover_preparation_is_a_no_effect_error(tmp_path, dry_run):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    before = _tree_bytes(vault)

    def refuse():
        raise ValueError("CLI cutover preflight failed: stale registry entries require explicit exclusion")

    result = upgrade.upgrade(str(vault), str(source), dry_run=dry_run, sync=False, prepare_cutover=refuse)

    assert result["status"] == "error"
    assert result["reason"] == "cutover_preflight"
    assert result["rollback_verified"] is True
    assert result["message"] == (
        "Upgrade refused — CLI cutover preflight failed: stale registry entries require explicit exclusion"
    )
    assert _tree_bytes(vault) == before


def test_the_prepared_cutover_commits_on_an_applying_run(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    calls = []

    def prepare():
        calls.append("prepared")
        return lambda _result: {"status": "ok", "committed": True}

    result = upgrade.upgrade(str(vault), str(source), sync=False, prepare_cutover=prepare)

    assert result["status"] == "ok", result
    assert calls == ["prepared"]
    assert result["cutover_commit"] == {"status": "ok", "committed": True}


def test_a_cutover_preflight_failure_is_a_json_error_from_main(tmp_path, monkeypatch, capsys):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")

    def fail_preflight(*_args, **_kwargs):
        raise ValueError("stale registry entries require explicit exclusion before cutover")

    monkeypatch.setattr(upgrade, "_prepare_cli_cutover", fail_preflight)
    monkeypatch.setattr(sys, "argv", ["upgrade.py", "--source", str(source), "--vault", str(vault), "--dry-run", "--json"])

    with pytest.raises(SystemExit) as exc:
        upgrade.main()

    assert exc.value.code == 1
    out, _err = capsys.readouterr()
    result = json.loads(out)
    assert result["reason"] == "cutover_preflight"
    assert "CLI cutover preflight failed: stale registry entries" in result["message"]
    assert _version(vault) == "1.0.0"


def test_a_matching_same_version_core_is_already_at_that_version(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)

    preview = _preview(vault, source)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert preview == result
    assert result["status"] == "skipped"
    assert result["message"].startswith("Already at 2.0.0")


def test_force_re_applies_a_matching_same_version_core_without_migrations(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)

    preview = _preview(vault, source, force=True)
    result = upgrade.upgrade(str(vault), str(source), force=True, sync=False)

    assert preview["status"] == result["status"] == "ok"
    assert _previewed_versions(preview) == [] and "migrations" not in result
    assert "core_mismatch" not in _warning_codes(result)
    assert _counter(vault, "count.txt") == 0
    assert result["router_compile"] == {"outcome": "ok"}


# --- 10. S8: copied files and changed directories are durable before VERSION ---------

def test_copied_files_and_changed_directories_are_fsynced_before_version(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain-core" / "obsolete.md").write_text("gone\n")
    synced = []
    real_fsync = os.fsync

    def spy(fd):
        info = os.fstat(fd)
        synced.append((info.st_ino, stat.S_ISDIR(info.st_mode)))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    core = vault / ".brain-core"
    version_index = synced.index((os.stat(core / "VERSION").st_ino, False))
    copied = [rel for rel in result["files_added"] + result["files_modified"] if rel != "VERSION"]
    assert copied
    for rel in copied:
        assert synced.index((os.stat(core / rel).st_ino, False)) < version_index, rel
    if os.name == "posix":
        for rel in result["files_added"]:
            parent = (core / rel).parent
            assert synced.index((os.stat(parent).st_ino, True)) < version_index, rel
        assert synced.index((os.stat(core).st_ino, True)) < version_index, "the removed file's directory"
        assert (os.stat(core).st_ino, True) in synced[version_index:], ".brain-core after VERSION"


# --- 11. N6: an interrupted previous upgrade is a warning only ----------------------

def _running_log(vault: Path, *, stage: str, old: str, new: str) -> None:
    (vault / ".brain" / "local" / "last-upgrade.json").write_text(json.dumps({
        "status": "running", "stage": stage, "old_version": old, "new_version": new,
    }))


def test_a_running_log_warns_and_does_not_change_selection(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_1_5_0.py": _counter_migration("a.txt"),
        "migrate_to_2_0_0.py": _counter_migration("b.txt"),
    })
    vault = _make_vault(tmp_path, "1.0.0")
    clean = _preview(vault, source)
    _running_log(vault, stage="post_compile_migrations", old="1.0.0", new="2.0.0")

    preview = _preview(vault, source)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert _previewed_versions(preview) == _previewed_versions(clean) == ["1.5.0", "2.0.0"]
    warning = next(w for w in preview["warnings"] if w["code"] == "interrupted_previous_upgrade")
    assert "1.0.0 → 2.0.0" in warning["message"] and "post_compile_migrations" in warning["message"]
    assert "resumes it" in warning["message"]
    assert result["status"] == "ok"
    assert [item["version"] for item in result["migrations"]] == ["1.5.0", "2.0.0"]


def test_a_post_commit_interruption_advises_a_same_version_re_apply(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    _running_log(vault, stage="dependency_sync", old="1.0.0", new="2.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "skipped"
    message = result["warnings"][0]["message"]
    assert "dependency_sync" in message
    assert "upgrade.py --force" in message


def test_a_pre_commit_interruption_at_the_installed_version_does_not_advise_force(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    _running_log(vault, stage="copy_brain_core", old="1.0.0", new="2.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "skipped"
    message = result["warnings"][0]["message"]
    assert "may be missing" in message
    assert "--force" not in message


# --- 12. a second machine's ledger is backfilled, not replayed ------------------------

def test_a_ledger_behind_a_committed_version_runs_only_the_new_migrations(tmp_path):
    source = _make_source(tmp_path, "4.0.0", migrations={
        "migrate_to_2_0_0.py": _counter_migration("a.txt"),
        "migrate_to_3_0_0.py": _counter_migration("b.txt"),
        "migrate_to_4_0_0.py": _counter_migration("c.txt"),
    })
    vault = _make_vault(tmp_path, "3.0.0")
    _write_ledger(vault, {"1.0.0": {"status": "ok"}})

    preview = _preview(vault, source)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert _previewed_versions(preview) == ["4.0.0"]
    assert [item["version"] for item in result["migrations"]] == ["4.0.0"]
    assert (_counter(vault, "a.txt"), _counter(vault, "b.txt"), _counter(vault, "c.txt")) == (0, 0, 1)
    ledger = json.loads((vault / ".brain" / "local" / "migrations.json").read_text())["migrations"]
    assert {key: entry["status"] for key, entry in ledger.items()} == {
        "1.0.0": "ok", "2.0.0": "backfilled", "3.0.0": "backfilled", "4.0.0": "ok",
    }


# --- H1: version validation at the boundary ----------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.0.0", (1, 0, 0)), ("0.70.10", (0, 70, 10)), ("01.0.0", None), ("1.0", None),
    ("1.0.0-dev", None), ("", None), (" 1.0.0", None),
])
def test_strict_version_accepts_only_x_y_z_without_leading_zeros(text, expected):
    assert upgrade._strict_version(text) == expected


@pytest.mark.parametrize("installed,source_version,which", [
    ("2.0.0", "1.0", "source"),
    ("1.0.0", "2.0.0-dev", "source"),
    ("01.0.0", "2.0.0", "installed"),
    ("", "2.0.0", "installed"),
])
@pytest.mark.parametrize("force", [False, True])
def test_an_unreadable_version_is_refused_with_no_effect(tmp_path, installed, source_version, which, force):
    source = _make_source(tmp_path, source_version, migrations={"migrate_to_1_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, installed)
    if which == "installed":
        _write_ledger(vault, {"3.0.0": {"status": "ok"}})
    before = _tree_bytes(vault)

    result = upgrade.upgrade(str(vault), str(source), force=force, sync=False)
    preview = _preview(vault, source, force=force)

    assert result["status"] == "error"
    assert result["reason"] == "version_unreadable"
    assert result["rollback_verified"] is True
    assert preview == result
    assert _tree_bytes(vault) == before
    bad_path = source / "VERSION" if which == "source" else vault / ".brain-core" / "VERSION"
    bad_text = source_version if which == "source" else installed
    assert str(bad_path) in result["message"] and repr(bad_text) in result["message"]


def test_an_absent_installed_version_is_not_unreadable(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain-core" / "VERSION").unlink()

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert result["old_version"] is None
    assert _version(vault) == "2.0.0"


@pytest.mark.parametrize("json_output", [False, True])
def test_an_unreadable_version_exits_one_from_main(tmp_path, monkeypatch, capsys, json_output):
    source = _make_source(tmp_path, "1.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    argv = ["upgrade.py", "--source", str(source), "--vault", str(vault)]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))

    with pytest.raises(SystemExit) as exc:
        upgrade.main()

    assert exc.value.code == 1
    out, err = capsys.readouterr()
    if json_output:
        assert json.loads(out)["reason"] == "version_unreadable"
    else:
        assert "not a version of the form X.Y.Z" in err
    assert _version(vault) == "2.0.0"


@pytest.mark.parametrize("which", ["source", "installed"])
def test_a_version_that_does_not_decode_is_unreadable_not_a_crash(tmp_path, which):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    bad_path = source / "VERSION" if which == "source" else vault / ".brain-core" / "VERSION"
    bad_path.write_bytes(b"\xff0.1.0\n")
    before = _tree_bytes(vault)

    result = _preview(vault, source)

    assert result["reason"] == "version_unreadable"
    assert str(bad_path) in result["message"]
    assert _tree_bytes(vault) == before


# --- H1: a damaged ledger is refused, never read as empty and overwritten ----------------

_DAMAGED_LEDGERS = [
    pytest.param('{"schema_version": 1, "migrations": {"3.0.0": {"status": "ok"', id="truncated"),
    pytest.param('[]', id="non-dict-root"),
    pytest.param('{"schema_version": 1, "migrations": [{"version": "3.0.0"}]}', id="non-dict-migrations"),
    pytest.param('{"schema_version": 1, "migrations": {"3.0.0": "ok"}}', id="non-dict-entry"),
]


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("text", _DAMAGED_LEDGERS)
def test_a_damaged_ledger_refuses_the_upgrade_before_the_seed_can_overwrite_it(tmp_path, text, force):
    """VERSION 1.0.0 + source 1.0.0 + a ledger recording 3.0.0 that cannot be read: refuse, do not backfill over it."""
    source = _make_source(tmp_path, "1.0.0", migrations={"migrate_to_1_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    _install_core(vault, source)
    ledger_path = vault / ".brain" / "local" / "migrations.json"
    ledger_path.write_text(text)
    before = _tree_bytes(vault)

    result = upgrade.upgrade(str(vault), str(source), force=force, sync=False)
    preview = _preview(vault, source, force=force)

    assert result["status"] == "error"
    assert result["reason"] == "ledger_unreadable"
    assert result["rollback_verified"] is True
    assert preview == result
    assert _tree_bytes(vault) == before, "the refusal runs before any write"
    assert ledger_path.read_text() == text
    message = result["message"]
    assert str(ledger_path) in message
    assert "Restore the file from a backup or your file-sync history" in message
    assert "Moving it aside" in message and "backfill" in message
    assert "discards the record of any content newer than VERSION" in message
    assert _counter(vault, "count.txt") == 0


def test_a_missing_ledger_is_no_records_and_the_upgrade_proceeds(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    assert not (vault / ".brain" / "local" / "migrations.json").exists()

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert _ledger_keys(vault) == {"2.0.0"}


@pytest.mark.parametrize("json_output", [False, True])
def test_a_damaged_ledger_exits_one_from_main(tmp_path, monkeypatch, capsys, json_output):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    (vault / ".brain" / "local" / "migrations.json").write_text("[]")
    argv = ["upgrade.py", "--source", str(source), "--vault", str(vault)]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))

    with pytest.raises(SystemExit) as exc:
        upgrade.main()

    assert exc.value.code == 1
    out, err = capsys.readouterr()
    if json_output:
        assert json.loads(out)["reason"] == "ledger_unreadable"
    else:
        assert "the migration ledger cannot be read" in err
    assert _version(vault) == "1.0.0"


# --- H2: the commit is split at the replace ----------------------------------------

def test_a_directory_fsync_failure_after_the_replace_is_a_warning_and_the_run_continues(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={"migrate_to_2_0_0.py": _counter_migration("count.txt")})
    vault = _make_vault(tmp_path, "1.0.0")
    target = str(vault / ".brain-core")
    real_fsync_directories = upgrade._fsync_directories

    def fail_only_for_the_core_directory(paths):
        if tuple(paths) == (target,):
            raise OSError("EIO: directory fsync failed")
        real_fsync_directories(paths)

    monkeypatch.setattr(upgrade, "_fsync_directories", fail_only_for_the_core_directory)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    assert "version_commit" not in result
    assert "version_commit_not_durable" in _warning_codes(result)
    assert result["router_compile"] == {"outcome": "ok"}
    assert _version(vault) == "2.0.0"
    assert _counter(vault, "count.txt") == 1

    rerun = upgrade.upgrade(str(vault), str(source), sync=False)

    assert rerun["status"] == "skipped"
    assert rerun["message"].startswith("Already at 2.0.0")


def test_the_commit_writes_the_source_version_bytes_captured_at_run_start(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")

    def rewrite_source_version(_result):
        (source / "VERSION").write_text("9.9.9\n")
        return {"status": "ok"}

    result = upgrade.upgrade(str(vault), str(source), sync=False, commit_callback=rewrite_source_version)

    assert result["status"] == "ok", result
    assert _version(vault) == "2.0.0"


# --- M1: every logged stage is classified ---------------------------------------------

def test_every_stage_the_upgrader_logs_is_a_named_classified_stage():
    import ast as _ast

    tree = _ast.parse(Path(upgrade.__file__).read_text(encoding="utf-8"))
    stages = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        name = node.func.id if isinstance(node.func, _ast.Name) else getattr(node.func, "attr", None)
        if name == "progress" and node.args:
            stages.append(node.args[0])
        elif name == "_write_upgrade_progress":
            stages.extend(kw.value for kw in node.keywords if kw.arg == "stage")
    assert stages, "no progress call sites found"
    constants = []
    for node in stages:
        if isinstance(node, _ast.Name) and node.id == "stage":
            continue  # the local progress helper forwarding its own parameter
        assert isinstance(node, _ast.Name) and node.id.startswith("STAGE_"), _ast.dump(node)
        constants.append(getattr(upgrade, node.id))
    assert constants and all(value in upgrade._STAGES for value in constants)
    assert set(upgrade._PRE_COMMIT_STAGES) < set(upgrade._STAGES)


def test_a_killed_same_version_re_apply_advises_the_same_re_apply(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    _running_log(vault, stage="copy_brain_core", old="2.0.0", new="2.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    message = result["warnings"][0]["message"]
    assert "same-version re-apply" in message
    assert "upgrade.py --force" in message
    assert "may be missing" not in message


def test_a_legacy_post_commit_stage_name_is_classified_as_committed(tmp_path):
    """Cores 0.36.8 to 0.48.9 logged ``semantic_repair``; it is not pre-commit, so the Core committed."""
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "2.0.0")
    _install_core(vault, source)
    _running_log(vault, stage="semantic_repair", old="1.0.0", new="2.0.0")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    message = result["warnings"][0]["message"]
    assert "Its Core is committed" in message
    assert "runtime.refresh-router, brain runtime repair or mcp.repair" in message


# --- M6: a damaged template capture refuses the run ---------------------------------

def test_a_damaged_template_capture_refuses_the_upgrade_with_no_effect(tmp_path):
    from _command_interface.authorisation_migration import TEMPLATE_CAPTURE_PATH

    source, vault = _authorisation_fixture(tmp_path)
    (vault / TEMPLATE_CAPTURE_PATH).write_text("{not json")
    before = _tree_bytes(vault / ".brain-core")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error"
    assert result["rollback_verified"] is True
    assert "capture existing authorisation" in result["message"]
    assert "not valid JSON" in result["message"]
    assert _tree_bytes(vault / ".brain-core") == before


# --- M11: the ledger-first restore records its failures -------------------------------

def test_a_failed_ledger_restore_is_recorded_with_a_recovery_path(tmp_path):
    source = _make_source(tmp_path, "2.0.0", migrations={
        "migrate_to_1_5_0.py": _counter_migration("first.txt"),
        "migrate_to_2_0_0.py": (
            "import os\n"
            "def migrate(vault_root):\n"
            "    ledger = os.path.join(vault_root, '.brain', 'local', 'migrations.json')\n"
            "    os.remove(ledger)\n"
            "    os.mkdir(ledger)\n"
            "    raise RuntimeError('boom after wedging the ledger')\n"
        ),
    })
    vault = _make_vault(tmp_path, "1.0.0")
    ledger_path = str(vault / ".brain" / "local" / "migrations.json")

    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error", result
    assert result["rollback_verified"] is False
    assert any(error.startswith("migration ledger: ") for error in result["rollback"]["errors"])
    assert ledger_path in result["recovery_paths"]


# --- L3, L4, L6: the copy phase -------------------------------------------------------

def test_newly_created_directories_and_their_parent_are_fsynced_before_version(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("directory fsync is a POSIX facility")
    source = _make_source(tmp_path, "2.0.0", migrations={})
    (source / "nested" / "deeper").mkdir(parents=True)
    (source / "nested" / "deeper" / "file.md").write_text("new\n")
    vault = _make_vault(tmp_path, "1.0.0")
    synced = []
    real_fsync = os.fsync

    def spy(fd):
        info = os.fstat(fd)
        synced.append((info.st_ino, stat.S_ISDIR(info.st_mode)))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "ok", result
    core = vault / ".brain-core"
    version_index = synced.index((os.stat(core / "VERSION").st_ino, False))
    for directory in (core, core / "nested", core / "nested" / "deeper"):
        assert synced.index((os.stat(directory).st_ino, True)) < version_index, directory


def test_a_copy_phase_fsync_failure_is_labelled_and_rolled_back(tmp_path, monkeypatch):
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    before = _tree_bytes(vault / ".brain-core")

    def fail(_paths):
        raise OSError("EIO")

    monkeypatch.setattr(upgrade, "_fsync_files", fail)
    result = upgrade.upgrade(str(vault), str(source), sync=False)

    assert result["status"] == "error"
    assert "could not make copied files durable" in result["message"]
    assert result["rollback_verified"] is True
    assert _tree_bytes(vault / ".brain-core") == before


def test_an_obsolete_file_that_cannot_be_removed_rolls_back(tmp_path):
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("needs POSIX permissions as a non-root user")
    source = _make_source(tmp_path, "2.0.0", migrations={})
    vault = _make_vault(tmp_path, "1.0.0")
    locked = vault / ".brain-core" / "locked"
    locked.mkdir()
    (locked / "obsolete.md").write_text("gone\n")
    before = _tree_bytes(vault / ".brain-core")
    locked.chmod(0o500)
    try:
        result = upgrade.upgrade(str(vault), str(source), sync=False)
    finally:
        locked.chmod(0o700)

    assert result["status"] == "error"
    assert result["message"].startswith("Upgrade rolled back — copy failed")
    assert _version(vault) == "1.0.0"
    assert _tree_bytes(vault / ".brain-core") == before


# --- L8 --------------------------------------------------------------------------------

def test_recording_anything_but_ok_or_skipped_is_an_invariant_violation(tmp_path):
    ledger = {"schema_version": 1, "migrations": {}}

    with pytest.raises(RuntimeError, match="returned 'blocked'; only ok or skipped results are recorded"):
        upgrade._record_migration_result(
            str(tmp_path), ledger, "1.0.0", "migrate_to_1_0_0.py", {"status": "blocked"},
        )

    assert ledger["migrations"] == {}
