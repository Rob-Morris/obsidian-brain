"""Tests for the ``empty_folders`` repair scope and its wiring."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

CLI_DIR = Path(__file__).resolve().parents[2] / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from _common import MutationLockError
import _repair_common as repair_common
import _repair_runtime as repair_runtime
import repair
from _application.artefact.repair import ArtefactRepairScope
from _application.vault import check as application_check
from _launcher import doctor as launcher_doctor

from _repair_helpers import _wiki_router


@pytest.fixture
def wiki_router(monkeypatch):
    router = _wiki_router()
    monkeypatch.setattr(repair_runtime.compile_router, "compile", lambda _vault: router)
    return router


def _empty_owner(repair_vault, *parts):
    path = repair_vault / "Wiki"
    for part in parts:
        path = path / part
    path.mkdir(parents=True)
    return path


class TestScopeWiring:
    def test_every_repair_scope_has_an_application_command(self):
        assert set(repair_common.REPAIR_SCOPES) <= set(application_check._REPAIR_COMMANDS)

    def test_every_repair_scope_has_a_doctor_command(self):
        assert set(repair_common.REPAIR_SCOPES) <= set(launcher_doctor._REPAIR_COMMANDS)

    def test_scope_identifier_is_uniform(self):
        assert ArtefactRepairScope.EMPTY_FOLDERS.value == "empty_folders"
        assert "empty_folders" in repair_common.REPAIR_SCOPES
        assert repair_common.REPAIR_SCOPES["empty_folders"]["description"]
        assert repair_common.REPAIR_SCOPES["empty_folders"]["check_message"]

    def test_every_repair_command_id_is_a_real_command(self):
        from _application.registry import current_application_catalogue
        from launcher_catalogue import LAUNCHER_CATALOGUE

        known = {entry.command_id for entry in current_application_catalogue().entries}
        known |= {entry.command_id for entry in LAUNCHER_CATALOGUE.entries}
        for table in (application_check._REPAIR_COMMANDS, launcher_doctor._REPAIR_COMMANDS):
            unknown = {scope: cid for scope, cid in table.items() if cid not in known}
            assert not unknown, f"repair command ids that resolve to no command: {unknown}"

    def test_cli_accepts_the_scope(self):
        args = repair.parse_args(["empty_folders", "--dry-run"])
        assert args.scope == "empty_folders"

    def test_run_scope_dispatches(self, repair_vault, wiki_router):
        result = repair_runtime.run_scope("empty_folders", repair_vault, dry_run=True)
        assert result["scope"] == "empty_folders"
        assert result["status"] == "noop"


class TestRepairEmptyFolders:
    def test_noop_when_nothing_is_vacated(self, repair_vault, wiki_router):
        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)
        assert result["status"] == "noop"
        assert result["steps"][-1]["status"] == "noop"

    def test_dry_run_lists_every_directory_and_junk_file(self, repair_vault, wiki_router):
        owner = _empty_owner(repair_vault, "project~brain", "+Adopted")
        (owner.parent / ".DS_Store").write_bytes(b"\x00")

        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=True)

        assert result["status"] == "planned"
        assert result["steps"][-1]["status"] == "planned"
        assert result["notes"] == [
            "Wiki/project~brain/",
            "Wiki/project~brain/+Adopted/",
            "Wiki/project~brain/.DS_Store",
        ]
        assert owner.is_dir(), "dry run must not mutate"

    def test_apply_removes_junk_and_directories_deepest_first(self, repair_vault, wiki_router):
        owner = _empty_owner(repair_vault, "project~brain", "+Adopted")
        (owner.parent / ".DS_Store").write_bytes(b"\x00")

        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert result["steps"][-1]["status"] == "changed"
        assert "Removed 1 vacated-empty folder(s)." == result["steps"][-1]["message"]
        assert result["notes"] == ["removed: Wiki/project~brain"]
        assert not (repair_vault / "Wiki" / "project~brain").exists()
        assert (repair_vault / "Wiki" / "Test Page.md").is_file()
        assert (repair_vault / "Wiki").is_dir()

    def test_directory_that_gained_content_is_skipped_not_deleted(
        self, repair_vault, wiki_router, monkeypatch
    ):
        owner = _empty_owner(repair_vault, "project~brain")
        findings = repair_runtime.scan_empty_artefact_folders(str(repair_vault), wiki_router)
        assert [f["path"] for f in findings] == ["Wiki/project~brain"]
        (owner / "Late Arrival.md").write_text("---\ntype: living/wiki\n---\n\nHi.\n")
        monkeypatch.setattr(
            repair_runtime, "scan_empty_artefact_folders", lambda *_args, **_kwargs: findings
        )

        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)

        assert result["status"] == "noop"
        assert "Skipped 1" in result["steps"][-1]["message"]
        assert result["notes"] == [
            "skipped: Wiki/project~brain — directory is no longer vacated-empty"
        ]
        assert (owner / "Late Arrival.md").is_file()

    def test_removal_failure_is_reported_as_partial(self, repair_vault, wiki_router, monkeypatch):
        _empty_owner(repair_vault, "project~brain")
        _empty_owner(repair_vault, "project~other")
        real_rmdir = os.rmdir

        def flaky_rmdir(path):
            if path.endswith("project~other"):
                raise OSError("busy")
            return real_rmdir(path)

        import _common._artefacts as artefacts_module
        monkeypatch.setattr(artefacts_module.os, "rmdir", flaky_rmdir)

        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)

        assert result["status"] == "partial"
        assert result["steps"][-1]["status"] == "error"
        assert "project~other" in result["steps"][-1]["message"]
        assert result["notes"] == [
            "removed: Wiki/project~brain",
            "failed: Wiki/project~other — busy",
        ]
        assert not (repair_vault / "Wiki" / "project~brain").exists()
        assert (repair_vault / "Wiki" / "project~other").is_dir()

    def test_unreadable_folder_aborts_before_any_removal(self, repair_vault, wiki_router):
        if os.geteuid() == 0:
            pytest.skip("permission bits do not bind root")
        removable = _empty_owner(repair_vault, "project~brain")
        locked = _empty_owner(repair_vault, "locked")
        locked.chmod(0o000)
        try:
            result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)
        finally:
            locked.chmod(0o755)

        assert result["status"] == "error"
        assert "Wiki/locked" in result["steps"][-1]["message"]
        assert removable.is_dir(), "a partial scan must not drive any removal"

    def test_partial_report_names_every_outcome(self, repair_vault, wiki_router, monkeypatch):
        _empty_owner(repair_vault, "project~a")
        gained = _empty_owner(repair_vault, "project~b")
        _empty_owner(repair_vault, "project~c")
        findings = repair_runtime.scan_empty_artefact_folders(str(repair_vault), wiki_router)
        assert [f["path"] for f in findings] == ["Wiki/project~a", "Wiki/project~b", "Wiki/project~c"]
        (gained / "Late.md").write_text("---\ntype: living/wiki\n---\n\nHi.\n")
        monkeypatch.setattr(
            repair_runtime, "scan_empty_artefact_folders", lambda *_args, **_kwargs: findings
        )
        real_rmdir = os.rmdir

        def flaky_rmdir(path):
            if path.endswith("project~c"):
                raise OSError("busy")
            return real_rmdir(path)

        import _common._artefacts as artefacts_module
        monkeypatch.setattr(artefacts_module.os, "rmdir", flaky_rmdir)

        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)

        assert result["status"] == "partial"
        assert result["steps"][-1]["message"].startswith(
            "Removed 1 folder(s), skipped 1; could not remove 1: Wiki/project~c: busy"
        )
        assert result["notes"] == [
            "removed: Wiki/project~a",
            "skipped: Wiki/project~b — directory is no longer vacated-empty",
            "failed: Wiki/project~c — busy",
        ]

    def test_type_root_and_archive_root_are_never_removed(self, repair_vault, wiki_router):
        (repair_vault / "_Archive").mkdir()
        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)
        assert result["status"] == "noop"
        assert (repair_vault / "Wiki").is_dir()
        assert (repair_vault / "_Archive").is_dir()

    def test_lock_contention_is_enveloped(self, repair_vault, wiki_router, monkeypatch):
        class BusyLock:
            def __enter__(self):
                raise MutationLockError("timed out acquiring mutation.lock")

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(
            repair_runtime, "vault_mutation_lock", lambda _vault: BusyLock()
        )

        result = repair_runtime.repair_empty_folders(repair_vault, dry_run=False)

        assert result["status"] == "error"
        assert "Vault is busy; retry" in result["steps"][-1]["message"]

    def test_locked_variant_does_not_reacquire_the_lock(self, repair_vault, wiki_router, monkeypatch):
        monkeypatch.setattr(
            repair_runtime,
            "vault_mutation_lock",
            lambda _vault: pytest.fail("locked variant must not take the lock"),
        )
        _empty_owner(repair_vault, "project~brain")

        result = repair_runtime.repair_empty_folders_locked(repair_vault, dry_run=False)

        assert result["status"] == "ok"
        assert not (repair_vault / "Wiki" / "project~brain").exists()
