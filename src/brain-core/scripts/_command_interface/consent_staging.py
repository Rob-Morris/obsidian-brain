"""Immutable prepared-input copies inside a trusted owner's private directory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import RLock

from _application.preparation import content_digest
from _bootstrap.consent_state import StoreConflictError
from _common import exclusive_file_lock, safe_write_via
from _staging import MAX_STAGED_BODY_BYTES, MAX_STAGING_BYTES, MAX_STAGING_FILES


class ConsentContentPins:
    """Pin content through child restarts without inheriting staging expiry.

    The directory is supplied by the process owner over its private channel;
    it must never be accepted as a business argument or public context ID.
    """

    def __init__(self, directory: Path, store, *, namespace: str, coordination_path: Path):
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise ValueError("pins require an existing trusted private owner directory")
        if not namespace or len(namespace.encode("utf-8")) > 128:
            raise ValueError("pins require a bounded descriptor namespace")
        if (not coordination_path.is_absolute() or coordination_path.is_symlink()
                or coordination_path.resolve().is_relative_to(directory.resolve())):
            raise ValueError("pin coordination must be outside the private owner directory")
        self.directory = directory
        self.coordination_path = coordination_path
        self.store = store
        self.namespace = namespace
        self._lock = RLock()

    def _key(self, source_key: str) -> str:
        if not isinstance(source_key, str) or not source_key:
            raise ValueError("pin source identity is required")
        digest = hashlib.sha256(source_key.encode()).hexdigest()
        return self._prefix + digest

    @property
    def _prefix(self) -> str:
        return "consent/pin/" + hashlib.sha256(self.namespace.encode()).hexdigest() + "/"

    def retain_content(self, source_key: str, content: bytes) -> dict:
        """Keep immutable bytes and an owner-scoped digest reference, never a body in RPC."""
        if not isinstance(content, bytes) or len(content) > MAX_STAGED_BODY_BYTES:
            raise ValueError("prepared body exceeds the existing staged-body byte limit")
        digest = content_digest(content)
        key = self._key(source_key)
        record = {"pin": digest[7:], "sha256": digest, "bytes": len(content)}
        with self._lock, exclusive_file_lock(self.coordination_path, create_parent=False, follow_symlinks=False):
            snapshot = self.store.snapshot((key,))
            prior = snapshot.values.get(key)
            if prior is not None:
                if prior != record:
                    raise ValueError("prepared content changed; prepare a new operation")
                self._read(record)
                return record
            self._collect_unreferenced()
            path = self.directory / record["pin"]
            if not path.exists():
                files = self._content_files()
                used = sum(item.stat().st_size for item in files)
                if len(files) >= MAX_STAGING_FILES or used + len(content) > MAX_STAGING_BYTES:
                    raise ValueError("prepared-input capacity reached; discard prepared operations")
                safe_write_via(path, lambda output: output.write(content),
                               bounds=self.directory, follow_symlinks=False)
                os.chmod(path, 0o600)
            elif self._read(record) != content:
                raise ValueError("prepared content digest mismatch")
            try:
                committed = self.store.compare_exchange(snapshot.versions, {key: record})
            except ValueError:
                # Local validation/capacity failures prove no commit. An uncertain
                # transport outcome instead retains bytes until the next collection.
                self._collect_unreferenced()
                raise
            if not committed:
                self._collect_unreferenced()
                raise ValueError("concurrent prepared-input change; prepare again")
            return record

    def discard(self) -> None:
        """Release this descriptor's pins, preserving bytes shared by other descriptors."""
        with self._lock, exclusive_file_lock(self.coordination_path, create_parent=False, follow_symlinks=False):
            keys = self._keys(self._prefix)
            snapshot = self.store.snapshot(keys)
            all_keys = self._keys("consent/pin/")
            retained = {record["pin"] for key, record in self.store.snapshot(all_keys).values.items()
                        if key not in snapshot.values}
            # All pin mutations share this file lock. Unrelated consent writes
            # do not invalidate a pin-only scan, and failed unlink is retryable.
            if keys and not self.store.compare_exchange(snapshot.versions, {key: None for key in keys}):
                raise ValueError("concurrent prepared-input disposal; retry disposal")
            self._remove_unreferenced(retained)

    def _content_files(self) -> tuple[Path, ...]:
        return tuple(item for item in self.directory.iterdir()
                     if len(item.name) == 64
                     and all(c in "0123456789abcdef" for c in item.name)
                     and item.is_file() and not item.is_symlink())

    def _collect_unreferenced(self) -> None:
        keys = self._keys("consent/pin/")
        retained = {record["pin"] for record in self.store.snapshot(keys).values.values()}
        self._remove_unreferenced(retained)

    def _remove_unreferenced(self, retained: set[str]) -> None:
        for path in self._content_files():
            if path.name not in retained:
                path.unlink(missing_ok=True)

    def _keys(self, prefix: str) -> tuple[str, ...]:
        # The global cursor may conflict with unrelated grant writes. Restart
        # only this read-only scan; never repeat a metadata mutation or unlink.
        for attempt in range(8):
            keys, after, revision = [], None, None
            try:
                while True:
                    page = self.store.list_keys(prefix=prefix, after=after, revision=revision)
                    keys.extend(page.keys)
                    if page.next_after is None:
                        return tuple(keys)
                    after, revision = page.next_after, page.revision
            except StoreConflictError:
                if attempt == 7:
                    raise
        raise AssertionError("unreachable pin scan")

    def read_pinned(self, source_key: str) -> bytes | None:
        """Resolve only this operation's pin, verifying its immutable content digest."""
        with self._lock, exclusive_file_lock(self.coordination_path,
                                            create_parent=False, follow_symlinks=False):
            key = self._key(source_key)
            record = self.store.snapshot((key,)).values.get(key)
            return None if record is None else self._read(record)

    def _read(self, record: dict) -> bytes:
        token = record.get("pin")
        if (not isinstance(token, str) or len(token) != 64
                or any(char not in "0123456789abcdef" for char in token)):
            raise ValueError("invalid prepared-input reference")
        path = self.directory / token
        if path.is_symlink() or not path.is_file():
            raise ValueError("prepared input is unavailable; prepare again")
        content = path.read_bytes()
        if len(content) != record["bytes"] or content_digest(content) != record["sha256"]:
            raise ValueError("prepared input changed; prepare again")
        return content
