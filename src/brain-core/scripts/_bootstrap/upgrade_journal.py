"""Write-ahead rollback journal of an upgrade, in machine-local state (DD-085).

The original bytes, or absence, of every vault path an upgrade may change are
durable here, under the machine state home, before the path may change, so a
killed run is restored by the next run from the same material an in-process rollback
uses. Using machine state home avoids a vault-relative journal location; it
does not enforce a sync exclusion for the configured state home.

Stdlib only, below ``_bootstrap``: ``upgrade.py`` loads it while it replaces
the rest of the scripts tree, and the launcher will read a vault's journal at
its own version. ``write_durably`` restates the atomic write of
``_common/_filesystem.safe_write`` (tmp, fsync, replace) for that reason; keep
the two aligned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Optional

from _bootstrap.paths import state_home

SCHEMA = "brain.upgrade-journal/1"
RECOVERY_SCHEMA = "brain.upgrade-recovery/1"
HEADER_FILE = "journal.json"
ENTRIES_FILE = "entries.jsonl"
BLOBS_DIR = "blobs"
JOURNALS_REL = os.path.join("brain", "upgrade-journals")
RECOVERY_REL = os.path.join("brain", "upgrade-recovery")
STAGE_PRE_COMPILE = "pre_compile"
STAGE_POST_COMPILE = "post_compile"
STAGES = (STAGE_PRE_COMPILE, STAGE_POST_COMPILE)
LOCK_FILE_SUFFIX = ".lock"
# Under .brain/local, never journalled. The retrieval outputs are rebuilt by
# their maintenance commands: the lexical index (_search/paths.OUTPUT_PATH)
# and the embedding sidecars (_application/_managed_preparation.SIDECARS,
# _semantic/runtime.EMBEDDINGS_META_REL), and semantic-models/
# (_semantic/model.SEMANTIC_MODELS_DIR_REL) is re-provisioned from
# semantic-model-manifest.json, which stays in. The command receipts
# (_command_interface/receipts.RECEIPT_DIRECTORY), operational diagnostics
# (_common/_operational_log.DIAGNOSTICS_REL) and staged drafts
# (_staging.STAGING_DIR) are not rebuildable, but no upgrade writes them and
# they record what happened, this run included, so a rollback must not
# rewind them; the upgrade log (upgrade._LAST_UPGRADE_FILE) is rewritten by
# every run, the recovering one included. tests/test_upgrade_journal.py pins
# this set to those constants.
EXCLUDED_LOCAL = frozenset({
    "retrieval-index.json",
    "type-embeddings.npy",
    "doc-embeddings.npy",
    "embeddings-meta.json",
    "semantic-models",
    "command-outcomes",
    "diagnostics",
    "staging",
    "last-upgrade.json",
})


class UpgradeJournalUnreadable(ValueError):
    """A journal is present but cannot be read as one, or names another vault."""


# ---------------------------------------------------------------------------
# Durable writes
# ---------------------------------------------------------------------------

def write_durably(path: str, content: bytes, *, file_mode: Optional[int] = None) -> None:
    """Atomic durable write; an explicit file mode is applied before fsync and replace."""
    target = os.path.realpath(path)
    parent = os.path.dirname(target) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(target) + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            if file_mode is not None:
                os.chmod(tmp_path, file_mode)
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def fsync_directories(paths: Iterable[str]) -> None:
    """Make directory entries durable; directory fsync is a POSIX facility."""
    if os.name != "posix":
        return
    synced = set()
    for path in paths:
        while not os.path.isdir(path):
            path = os.path.dirname(path)
        if path in synced:
            continue
        synced.add(path)
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def make_directories_durably(path: str) -> None:
    """``makedirs`` whose new entries are durable up to the first directory that already existed."""
    missing = []
    current = os.path.abspath(path)
    while not os.path.isdir(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    if not missing:
        return
    os.makedirs(path, exist_ok=True)
    fsync_directories([*reversed(missing), current])


# ---------------------------------------------------------------------------
# Location and scope
# ---------------------------------------------------------------------------

def _vault_digest(vault_root: str) -> str:
    """One key per vault, by its resolved path, so a moved vault never matches."""
    return hashlib.sha256(os.path.realpath(vault_root).encode("utf-8")).hexdigest()[:16]


def journal_directory(vault_root: str) -> str:
    return os.path.join(str(state_home()), JOURNALS_REL, _vault_digest(vault_root))


def recovery_root(vault_root: str) -> str:
    """Where a restore keeps the bytes it found changed since the journal was written."""
    return os.path.join(str(state_home()), RECOVERY_REL, _vault_digest(vault_root))


def is_excluded(path: str) -> bool:
    """Lock endpoints (whose restore would replace the inode under a holder) and the stores above."""
    parts = Path(path).parts
    if parts and parts[-1].endswith(LOCK_FILE_SUFFIX):
        return True
    return any(
        parts[index] in EXCLUDED_LOCAL and parts[index - 2:index] == (".brain", "local")
        for index in range(2, len(parts))
    )


def walk_root(root: str) -> list[tuple[str, list[str], list[str]]]:
    """Walk ``root`` top-down, pruning what the journal never holds; reverse it for bottom-up."""
    walked = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not is_excluded(os.path.join(dirpath, name))]
        files = [name for name in filenames if not is_excluded(os.path.join(dirpath, name))]
        walked.append((dirpath, list(dirnames), files))
    return walked


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False


def _normalised_absolute(path) -> bool:
    return isinstance(path, str) and os.path.isabs(path) and os.path.normpath(path) == path


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------

class UpgradeJournal:
    """The original state of every path an upgrade may change, durable before the path does.

    Blobs are content-addressed, so identical content is written once. Entries
    are appended; the first entry for a path in a stage wins. A tree root's
    listing is appended after the file entries of its batch, so a root that is
    durable vouches for a complete batch and a torn one restores files without
    removing anything. The header is written first and removed first, so a
    directory without one is a discarded remnant, never a journal.
    """

    def __init__(self, directory: str, header: dict, given_vault: str, entries: Optional[list[dict]] = None):
        self.directory = directory
        self.header = header
        self._given_vault = given_vault
        self._entries: list[dict] = entries if entries is not None else []
        self._captured: set[tuple[str, str]] = {
            (entry["stage"], entry["path"]) for entry in self._entries if "path" in entry
        }
        self._roots: set[tuple[str, str]] = {
            (entry["stage"], entry["root"]) for entry in self._entries if "root" in entry
        }

    @property
    def vault(self) -> str:
        return self.header["vault"]

    @property
    def old_version(self) -> Optional[str]:
        return self.header["old_version"]

    @property
    def new_version(self) -> str:
        return self.header["new_version"]

    @property
    def header_path(self) -> str:
        return os.path.join(self.directory, HEADER_FILE)

    def _canonical(self, path: str) -> str:
        """Spell a path under the vault as the header does, whatever spelling the caller used."""
        path = os.path.abspath(path)
        if self._given_vault != self.vault and _within(path, self._given_vault):
            return os.path.normpath(os.path.join(self.vault, os.path.relpath(path, self._given_vault)))
        return path

    @classmethod
    def open(cls, vault_root: str, old_version: Optional[str], new_version: str) -> "UpgradeJournal":
        """Start an empty journal for this run.

        A journal already present is refused, because the caller recovers it
        first; a headerless remnant is removed, because its blobs may be torn.
        """
        directory = journal_directory(vault_root)
        if os.path.exists(os.path.join(directory, HEADER_FILE)):
            raise FileExistsError(f"a rollback journal is already present at {directory}")
        if os.path.isdir(directory):
            shutil.rmtree(directory)
        blobs = os.path.join(directory, BLOBS_DIR)
        make_directories_durably(blobs)
        with open(os.path.join(directory, ENTRIES_FILE), "wb") as handle:
            os.fsync(handle.fileno())
        header = {
            "schema": SCHEMA,
            "vault": os.path.realpath(vault_root),
            "old_version": old_version,
            "new_version": new_version,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        write_durably(os.path.join(directory, HEADER_FILE), (json.dumps(header, indent=2) + "\n").encode("utf-8"))
        fsync_directories((directory,))
        return cls(directory, header, os.path.abspath(vault_root))

    @classmethod
    def load(cls, vault_root: str) -> Optional["UpgradeJournal"]:
        """Read the vault's leftover journal without changing it: None when there is none, raise when it cannot be trusted."""
        directory = journal_directory(vault_root)
        header_path = os.path.join(directory, HEADER_FILE)
        if not os.path.exists(header_path):
            return None
        try:
            with open(header_path, "r", encoding="utf-8") as handle:
                header = json.load(handle)
        except (OSError, ValueError) as exc:
            raise UpgradeJournalUnreadable(f"{header_path}: {exc}") from exc
        if (
            not isinstance(header, dict)
            or header.get("schema") != SCHEMA
            or not isinstance(header.get("vault"), str)
            or not isinstance(header.get("new_version"), str)
            or not (header.get("old_version") is None or isinstance(header["old_version"], str))
        ):
            raise UpgradeJournalUnreadable(f"{header_path}: not an upgrade journal header")
        if header["vault"] != os.path.realpath(vault_root):
            raise UpgradeJournalUnreadable(f"{header_path}: records another vault, {header['vault']}")
        return cls(directory, header, os.path.abspath(vault_root), cls._read_entries(directory, header["vault"]))

    # --- capture -----------------------------------------------------------------

    def capture(self, stage: str, paths: Iterable[str]) -> None:
        """Make the original state of ``paths`` durable before any of them may change."""
        self._append(*self._file_entries(stage, paths))

    def capture_tree(self, stage: str, root: str) -> None:
        """Capture every file under ``root`` and its listing, so restore can remove what the run creates."""
        root = self._canonical(root)
        if (stage, root) in self._roots:
            return
        self._roots.add((stage, root))
        dirs, files = [], []
        if os.path.exists(root):
            for dirpath, _dirnames, filenames in walk_root(root):
                dirs.append(dirpath)
                files.extend(os.path.join(dirpath, name) for name in filenames)
        lines, new_blobs = self._file_entries(stage, files)
        # The root line last: it authorises removing what is not listed, so it
        # must never be durable ahead of the listing it vouches for.
        self._append([*lines, {"stage": stage, "root": root, "dirs": dirs}], new_blobs)

    def _file_entries(self, stage: str, paths: Iterable[str]) -> tuple[list[dict], list[str]]:
        lines, new_blobs = [], []
        for path in paths:
            path = self._canonical(path)
            if (stage, path) in self._captured or is_excluded(path):
                continue
            self._captured.add((stage, path))
            if not os.path.exists(path):
                lines.append({"stage": stage, "path": path, "exists": False, "blob": None})
                continue
            with open(path, "rb") as handle:
                content = handle.read()
            digest = hashlib.sha256(content).hexdigest()
            blob = os.path.join(self.directory, BLOBS_DIR, digest)
            if not os.path.exists(blob):
                write_durably(blob, content)
                new_blobs.append(blob)
            lines.append({"stage": stage, "path": path, "exists": True, "blob": digest})
        return lines, new_blobs

    def _append(self, lines: list[dict], new_blobs: list[str]) -> None:
        """Blobs are durable (file and directory) before the entries that name them are."""
        if not lines:
            return
        if new_blobs:
            fsync_directories((os.path.join(self.directory, BLOBS_DIR),))
        with open(os.path.join(self.directory, ENTRIES_FILE), "ab") as handle:
            handle.write("".join(json.dumps(line) + "\n" for line in lines).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        self._entries.extend(lines)

    # --- read ----------------------------------------------------------------------

    @classmethod
    def _read_entries(cls, directory: str, vault: str) -> list[dict]:
        """Parse and validate the entries; this is the trust boundary for everything a restore will do.

        A vault path changes only after its line is durable, so an unterminated
        tail guarded nothing and is dropped. Every path is absolute and
        normalised. A tree root must lie under the vault, because a root
        authorises removing whatever is not listed beneath it; a file entry may
        lie outside it (0.27.6 declares client configuration in the home
        directory) and only ever rewrites that exact path to the captured bytes.
        Blob content is checked against its name when it is read.
        """
        path = os.path.join(directory, ENTRIES_FILE)
        blobs = os.path.join(directory, BLOBS_DIR)
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            raise UpgradeJournalUnreadable(f"{path}: {exc}") from exc
        entries = []
        for number, line in enumerate(raw.split(b"\n")[:-1], 1):
            where = f"{path}:{number}"
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise UpgradeJournalUnreadable(f"{where}: {exc}") from exc
            if not isinstance(entry, dict) or entry.get("stage") not in STAGES:
                raise UpgradeJournalUnreadable(f"{where}: not a journal entry")
            if "root" in entry:
                root, dirs = entry["root"], entry.get("dirs")
                if (
                    not _normalised_absolute(root)
                    or not _within(root, vault)
                    or not isinstance(dirs, list)
                    or any(not _normalised_absolute(item) or not _within(item, root) for item in dirs)
                ):
                    raise UpgradeJournalUnreadable(f"{where}: not a journal root entry under {vault}")
            elif not _normalised_absolute(entry.get("path")) or not isinstance(entry.get("exists"), bool):
                raise UpgradeJournalUnreadable(f"{where}: not a journal entry")
            elif entry["exists"]:
                blob = entry.get("blob")
                if (
                    not isinstance(blob, str)
                    or len(blob) != 64
                    or blob.strip("0123456789abcdef")
                    or not os.path.isfile(os.path.join(blobs, blob))
                ):
                    raise UpgradeJournalUnreadable(f"{where}: blob {blob!r} is missing")
            entries.append(entry)
        return entries

    def _blob_bytes(self, digest: str) -> bytes:
        blob = os.path.join(self.directory, BLOBS_DIR, digest)
        try:
            with open(blob, "rb") as handle:
                content = handle.read()
        except OSError as exc:
            raise UpgradeJournalUnreadable(f"{blob}: {exc}") from exc
        if hashlib.sha256(content).hexdigest() != digest:
            raise UpgradeJournalUnreadable(f"{blob}: content does not match its name")
        return content

    def stage_entries(self, stage: str) -> tuple[dict[str, Optional[str]], dict[str, set[str]]]:
        """The stage's captured paths (to their blob digest, None when absent) and roots, without reading blobs."""
        files: dict[str, Optional[str]] = {}
        roots: dict[str, set[str]] = {}
        for entry in self._entries:
            if entry["stage"] != stage:
                continue
            if "root" in entry:
                roots.setdefault(entry["root"], set(entry["dirs"]))
            elif entry["path"] not in files:
                files[entry["path"]] = entry["blob"] if entry["exists"] else None
        return files, roots

    def stage_snapshots(self, stage: str) -> tuple[dict[str, dict], dict[str, set[str]]]:
        """The stage's captured state in the shape ``restore_snapshots`` takes; blob bytes are read here."""
        files, roots = self.stage_entries(stage)
        snapshots = {
            path: {"exists": True, "content": self._blob_bytes(digest)} if digest is not None else {"exists": False}
            for path, digest in files.items()
        }
        return snapshots, roots

    def path_state(self, stage: str, path: str) -> Optional[dict]:
        """One path's captured state in a stage, or None when the stage does not hold it."""
        files, _roots = self.stage_entries(stage)
        path = self._canonical(path)
        if path not in files:
            return None
        digest = files[path]
        return {"exists": True, "content": self._blob_bytes(digest)} if digest is not None else {"exists": False}

    def discard(self) -> None:
        """Remove the header durably first, so a kill mid-removal leaves a remnant, then the rest."""
        try:
            os.remove(self.header_path)
        except FileNotFoundError:
            pass
        fsync_directories((self.directory,))
        shutil.rmtree(self.directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

class RecoveryStore:
    """Bytes a restore found that it did not journal, kept beside the journal before they are overwritten.

    A restore never destroys bytes it did not journal: whatever changed since
    the journal was written (the run's own half-applied writes, or edits made
    between a kill and the next upgrade) is copied here, with a manifest from
    vault path to copy, and made durable before the first restore write. The
    directory is created on first use, so a restore that changes nothing
    leaves nothing behind.
    """

    def __init__(self, root: str, vault: str):
        self.root = root
        self.vault = vault
        self.directory: Optional[str] = None
        self.preserved: dict[str, str] = {}
        self.originals: dict[str, str] = {}
        self._sequence = 0

    @classmethod
    def for_vault(cls, vault_root: str) -> "RecoveryStore":
        return cls(recovery_root(vault_root), os.path.realpath(vault_root))

    def _copy(self, kind: str, path: str, content: bytes) -> str:
        if self.directory is None:
            make_directories_durably(self.root)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.directory = tempfile.mkdtemp(prefix=f"{stamp}-", dir=self.root)
            os.mkdir(os.path.join(self.directory, "files"))
        self._sequence += 1
        relative = os.path.join("files", f"{self._sequence:04d}-{os.path.basename(path) or 'file'}{kind}")
        write_durably(os.path.join(self.directory, relative), content)
        return relative

    def preserve(self, path: str, content: bytes) -> None:
        """Keep the current bytes of ``path`` before the restore replaces or removes them."""
        self.preserved[path] = self._copy("", path, content)

    def keep_original(self, path: str, content: bytes) -> Optional[str]:
        """Keep the journalled bytes of a path whose restore failed; returns the copy's path."""
        try:
            self.originals[path] = self._copy(".original", path, content)
        except OSError:
            return None
        return os.path.join(self.directory, self.originals[path])

    def commit(self) -> None:
        """Write the manifest and make the directory durable; a no-op when nothing was kept."""
        if self.directory is None:
            return
        manifest = {
            "schema": RECOVERY_SCHEMA,
            "vault": self.vault,
            "preserved": self.preserved,
            "originals": self.originals,
        }
        write_durably(os.path.join(self.directory, "manifest.json"), (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        fsync_directories((os.path.join(self.directory, "files"), self.directory, self.root))


@dataclass(frozen=True)
class RestoreReport:
    """Complete outcome of restoring journalled vault state."""

    verified: bool
    restored_paths: frozenset[str] = frozenset()
    errors: tuple[str, ...] = ()
    recovery_paths: tuple[str, ...] = ()

    def labelled(self, label: str) -> "RestoreReport":
        return RestoreReport(
            self.verified, self.restored_paths,
            tuple(f"{label}: {error}" for error in self.errors), self.recovery_paths,
        )

    def merged(self, other: "RestoreReport") -> "RestoreReport":
        return RestoreReport(
            self.verified and other.verified,
            self.restored_paths | other.restored_paths,
            self.errors + other.errors,
            tuple(dict.fromkeys(self.recovery_paths + other.recovery_paths)),
        )


def _current_bytes(path: str) -> Optional[bytes]:
    """The bytes at ``path`` now; None when there is no file to read (absent, a directory, unreadable)."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def restore_snapshots(
    snapshots: dict[str, dict],
    *,
    roots: Optional[dict[str, set[str]]] = None,
    store: Optional[RecoveryStore] = None,
) -> RestoreReport:
    """Return every path to its captured state and remove what the run created under each root.

    Three passes. Plan: compare the current bytes of every captured path with
    the snapshot, and list the files and directories under each root that the
    listing does not know, so a path already in its captured state is not
    rewritten (which would churn file-sync and editor inodes). Preserve: copy
    every byte the restore will replace or remove into ``store`` and make it
    durable. Apply: restore the files, then remove the unknown files, then the
    unknown directories bottom-up, and verify the result. Every failure is
    attempted past and reported, with the journalled bytes kept for the paths
    whose restore failed.
    """
    roots = roots or {}
    errors: list[str] = []
    recovery_paths: list[str] = []
    restored: set[str] = set()
    failed: set[str] = set()

    def fail(action: str, path: str, exc: BaseException, state: Optional[dict] = None) -> None:
        if path in failed:
            return
        failed.add(path)
        errors.append(f"{action} {path}: {exc}")
        recovery_paths.append(path)
        if state is not None and state.get("exists") and store is not None:
            copy = store.keep_original(path, state["content"])
            if copy is None:
                errors.append(f"could not preserve original bytes for {path}")
            else:
                recovery_paths.append(copy)

    # Plan: what the restore must write, remove or prune, and what it will overwrite.
    writes: dict[str, bytes] = {}
    removals: list[str] = []
    prunes: list[str] = []
    foreign: dict[str, bytes] = {}
    for path, state in snapshots.items():
        current = _current_bytes(path)
        if state.get("exists"):
            if current == state["content"]:
                continue
            writes[path] = state["content"]
        elif current is None:
            continue
        else:
            removals.append(path)
        if current is not None:
            foreign[path] = current
    walked_roots = {
        root: walk_root(root) if os.path.exists(root) else [] for root in roots
    }
    for root, original_dirs in roots.items():
        for dirpath, dirnames, filenames in reversed(walked_roots[root]):
            for name in filenames:
                path = os.path.join(dirpath, name)
                if path not in snapshots:
                    removals.append(path)
                    current = _current_bytes(path)
                    if current is not None:
                        foreign[path] = current
            prunes.extend(os.path.join(dirpath, name) for name in dirnames if os.path.join(dirpath, name) not in original_dirs)
        if walked_roots[root] and root not in original_dirs:
            prunes.append(root)

    # Preserve, durably, before the first restore write.
    if store is not None and foreign:
        for path, content in foreign.items():
            store.preserve(path, content)
        store.commit()

    # Apply.
    for path, content in writes.items():
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            write_durably(path, content)
            restored.add(path)
        except BaseException as exc:
            if _current_bytes(path) != content:
                fail("restore original file", path, exc, snapshots[path])
    for path in removals:
        try:
            os.remove(path)
            restored.add(path)
        except FileNotFoundError:
            continue
        except BaseException as exc:
            fail("remove introduced file", path, exc)
    for path in prunes:
        try:
            os.rmdir(path)
            restored.add(path)
        except BaseException as exc:
            if os.path.exists(path):
                fail("remove new directory", path, exc)

    # Verify.
    for path, state in snapshots.items():
        if path in failed:
            continue
        current = _current_bytes(path)
        if (current == state["content"]) if state.get("exists") else (current is None and not os.path.exists(path)):
            continue
        fail("verify restored file", path, OSError("restored state does not match the captured snapshot"), state)
    expected_by_root: dict[str, set[str]] = {root: set() for root in roots}
    for path, state in snapshots.items():
        if state.get("exists"):
            for root in roots:
                if _within(path, root):
                    expected_by_root[root].add(path)
    for root, original_dirs in roots.items():
        if not os.path.exists(root):
            if original_dirs:
                fail("verify restored directory", root, OSError("original snapshot root is missing after rollback"))
            continue
        walked = walk_root(root)
        for dirpath, _dirnames, filenames in walked:
            for name in filenames:
                path = os.path.join(dirpath, name)
                if path not in expected_by_root[root]:
                    fail("verify removed file", path, OSError("introduced file remains after rollback"))
        for path in sorted({dirpath for dirpath, _dirnames, _filenames in walked} ^ original_dirs):
            fail("verify restored directory", path, OSError("directory topology differs from the captured snapshot"))
    return RestoreReport(not errors, frozenset(restored), tuple(errors), tuple(dict.fromkeys(recovery_paths)))


def restore_journal(journal: UpgradeJournal, ledger_path: str, store: RecoveryStore) -> RestoreReport:
    """Restore everything the journal holds: the ledger first, then each stage newest first.

    A migration records itself after its effects land, so unwinding must
    unrecord before it undoes; an interrupted restore then leaves the ledger
    at or behind the content. Stage scopes can overlap (the later one may hold
    an earlier migration's intermediate bytes), so each stage is restored and
    verified before its predecessor restores older bytes over the same paths.
    Blob bytes are read one stage at a time.
    """
    ledger_path = journal._canonical(ledger_path)
    report = RestoreReport(True)
    ledger = journal.path_state(STAGE_PRE_COMPILE, ledger_path)
    if ledger is not None:
        report = report.merged(restore_snapshots({ledger_path: ledger}, store=store).labelled("migration ledger"))
    for stage, label in ((STAGE_POST_COMPILE, "post-compile state"), (STAGE_PRE_COMPILE, "pre-compile state")):
        snapshots, roots = journal.stage_snapshots(stage)
        if not snapshots and not roots:
            continue
        report = report.merged(restore_snapshots(snapshots, roots=roots, store=store).labelled(label))
    return report
