"""Durable byte accounting under the result store's shared parent lock.

Every directory has a fixed-size subtotal, including a reserved counter-rewrite
slot. A one-byte parent marker is fsynced dirty before data changes. Clean
subtotals are published before clearing it. Missing/dirty accounting is rebuilt
from disk; clean transactions read only the touched directory ancestry. The
caller must hold the same cross-process lock for transactions and data access.
External filesystem administration must mark the parent dirty before changing
stored bytes; changes by hostile same-user filesystem writers are out of scope.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import shutil
import stat

from _bootstrap.private_files import create_dir

COUNTER = '.result-usage'
COUNTER_TEMP = '.result-usage.new'
MARKER = '.result-accounting'
COUNTER_BYTES = 128
DIRECTORY_CHARGE = 2 * COUNTER_BYTES
LOCK_RESERVE = 128
_MAX_TOTAL = 2**63 - 1


class ResultAccountingError(Exception):
    """A content-safe quota, filesystem or recovery failure."""

    def __init__(self, code, message, *, capacity_scope=None, actual_limit=None,
                 representation_scope=None):
        """Expose only bounded primitive limit metadata alongside a safe message."""
        self.code, self.message = code, message
        self.capacity_scope = capacity_scope if capacity_scope in ('brain', 'owner', 'result') else None
        self.representation_scope = representation_scope if representation_scope in ('page', 'metadata') else None
        self.actual_limit = actual_limit if type(actual_limit) is int and 1 <= actual_limit <= _MAX_TOTAL else None
        super().__init__(message)


def _mutation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            self._failed = True
            raise
    return guarded


class FileResultAccounting:
    """Track constant-size root/binding/owner/result and directory subtotals.

    Bounds are byte ceilings, not expiry rules. Counter and replacement reserves
    count towards capacity even while the replacement slot is unused. All
    siblings, including inaccessible old bindings and unfinished writes, remain
    charged. In-memory state exists only during the caller's locked transaction.
    """

    def __init__(self, root, *, brain_bytes, owner_bytes, result_bytes):
        """Configure validated scope ceilings; perform no I/O before lock acquisition."""
        self.root = Path(root)
        self.brain_bytes = brain_bytes
        self.owner_bytes = owner_bytes
        self.result_bytes = result_bytes
        self._active = False
        self._dirty = False
        self._failed = False
        self._totals = {}
        self._changed = set()
        self._epoch = 0

    def _relative(self, path):
        path = Path(path)
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ResultAccountingError('unsafe_path', 'Accounting path is outside result storage.') from None
        if '..' in relative.parts:
            raise ResultAccountingError('unsafe_path', 'Accounting path is outside result storage.')
        return path, relative

    def _data(self, path):
        path, relative = self._relative(path)
        if (any(part in (COUNTER, COUNTER_TEMP, MARKER) for part in relative.parts)
                or path == self.root / '.lock'):
            raise ResultAccountingError('unsafe_path', 'Reserved result accounting endpoint.')
        return path

    def _safe(self, path, *, directory=False):
        for parent in reversed((path, *path.parents)):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ResultAccountingError('unsafe_path', 'Result accounting contains an unsafe path.')
        info = path.lstat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        if not valid:
            raise ResultAccountingError('unsafe_path', 'Result accounting contains an invalid file type.')
        return info

    def _sync(self, directory):
        if os.name != 'nt':
            fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _read_control(self, path, limit):
        self._safe(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ResultAccountingError('storage_error', 'Result accounting metadata exceeds its fixed bound.')
        return raw

    def _mark(self, value):
        path = self.root / MARKER
        if path.exists() or path.is_symlink():
            self._safe(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            if stream.write(value) != 1:
                raise OSError('Incomplete accounting marker write')
            stream.truncate(1)
            stream.flush()
            os.fsync(stream.fileno())
        self._sync(self.root)

    def _begin_change(self):
        if not self._active:
            raise ResultAccountingError('storage_error', 'Result accounting requires a locked transaction.')
        if not self._dirty:
            self._mark(b'1')
            self._dirty = True

    def _load_total(self, path):
        raw = self._read_control(path / COUNTER, COUNTER_BYTES)
        if len(raw) != COUNTER_BYTES:
            raise ValueError('counter size')
        value = json.loads(raw)
        total = value['bytes']
        if value.get('version') != 1 or type(total) is not int or not DIRECTORY_CHARGE <= total <= _MAX_TOTAL:
            raise ValueError('counter shape')
        return total

    def _save_total(self, path, total):
        if not DIRECTORY_CHARGE <= total <= _MAX_TOTAL:
            raise ResultAccountingError('storage_error', 'Result accounting subtotal is invalid.')
        raw = json.dumps({'version': 1, 'bytes': total}, separators=(',', ':')).encode().ljust(COUNTER_BYTES, b' ')
        if len(raw) != COUNTER_BYTES:
            raise ResultAccountingError('storage_error', 'Result accounting subtotal exceeds its fixed bound.')
        temporary = path / COUNTER_TEMP
        if temporary.exists() or temporary.is_symlink():
            self._safe(temporary)
        target = path / COUNTER
        if target.exists() or target.is_symlink():
            self._safe(target)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            if stream.write(raw) != len(raw):
                raise OSError('Incomplete accounting counter write')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        self._sync(path)

    def _recover(self):
        self._begin_change()
        self._totals.clear()
        self._changed.clear()
        self._epoch += 1

        def visit(directory):
            self._safe(directory, directory=True)
            total = DIRECTORY_CHARGE
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = directory / entry.name
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise ResultAccountingError('unsafe_path', 'Result accounting contains an unsafe path.')
                    if stat.S_ISDIR(info.st_mode):
                        total += visit(path)
                    else:
                        self._safe(path)
                        if entry.name in (COUNTER, COUNTER_TEMP):
                            if info.st_size > COUNTER_BYTES:
                                raise ResultAccountingError('storage_error', 'Result accounting metadata exceeds its fixed bound.')
                        elif directory == self.root and entry.name == MARKER:
                            if info.st_size != 1:
                                raise ResultAccountingError('storage_error', 'Result accounting marker is invalid.')
                        elif directory == self.root and entry.name == '.lock':
                            if info.st_size > LOCK_RESERVE:
                                raise ResultAccountingError('storage_error', 'Result lock metadata exceeds its reserved capacity.')
                        else:
                            total += info.st_size
            if directory == self.root:
                total += LOCK_RESERVE + 1
            self._save_total(directory, total)
            return total

        total = visit(self.root)
        self._totals[self.root] = total
        self._mark(b'0')
        self._dirty = False

    @contextmanager
    def transaction(self):
        """Use fresh locked counters; reconcile once after interruption or missing state.

        An interrupted mutation leaves the marker dirty. Recovery preserves all
        data and counts partial/stale bytes; over-limit stores can still delete
        output, but subsequent growth is refused. No per-instance warmup scan is
        performed when another process has already published clean counters.
        """
        if self._active:
            raise ResultAccountingError('storage_error', 'Nested result accounting transactions are invalid.')
        self._active = True
        self._dirty = False
        self._failed = False
        self._totals = {}
        self._changed = set()
        try:
            self._safe(self.root, directory=True)
            lock = self.root / '.lock'
            if self._safe(lock).st_size > LOCK_RESERVE:
                raise ResultAccountingError('storage_error', 'Result lock metadata exceeds its reserved capacity.')
            try:
                clean = self._read_control(self.root / MARKER, 1) == b'0'
                if clean:
                    self._totals[self.root] = self._load_total(self.root)
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                clean = False
            if not clean:
                if (not (self.root / COUNTER).exists() and not (self.root / MARKER).exists()
                        and self.brain_bytes < DIRECTORY_CHARGE + LOCK_RESERVE + 1):
                    raise self._capacity_error(self.root, self.brain_bytes)
                self._recover()
            yield self
            if self._failed and self._dirty:
                raise ResultAccountingError('storage_error', 'An interrupted accounting transaction requires recovery.')
            if self._dirty:
                for path in sorted(self._changed, key=lambda p: len(p.parts), reverse=True):
                    self._save_total(path, self._totals[path])
                self._mark(b'0')
                self._dirty = False
        finally:
            self._active = False
            self._totals.clear()
            self._changed.clear()

    def total(self, directory):
        """Read a directory subtotal without enumerating its descendants."""
        directory, _ = self._relative(directory)
        if not self._active:
            raise ResultAccountingError('storage_error', 'Result accounting requires a locked transaction.')
        if directory not in self._totals:
            self._safe(directory, directory=True)
            try:
                self._totals[directory] = self._load_total(directory)
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                if self._dirty:
                    raise ResultAccountingError('storage_error', 'Result accounting needs recovery before further writes.') from None
                self._recover()
                self._totals[directory] = self._load_total(directory)
        return self._totals[directory]

    def _ancestry(self, directory):
        directory, relative = self._relative(directory)
        return [self.root.joinpath(*relative.parts[:length]) for length in range(len(relative.parts) + 1)]

    def _limit(self, path):
        depth = len(path.relative_to(self.root).parts)
        return self.brain_bytes if depth == 0 else self.owner_bytes if depth == 2 else self.result_bytes if depth == 3 else None

    def _capacity_error(self, path, limit):
        depth = len(path.relative_to(self.root).parts)
        scope = {0: 'brain', 2: 'owner', 3: 'result'}.get(depth)
        return ResultAccountingError('capacity_exceeded',
            'Retention capacity exceeded; explicitly clear output or raise storage limits.',
            capacity_scope=scope, actual_limit=limit)

    def _adjust(self, changes, *, enforce=True):
        epoch = self._epoch
        updated = {path: self.total(path) + delta for path, delta in changes.items() if delta}
        if self._epoch != epoch:
            updated = {path: self.total(path) + delta for path, delta in changes.items() if delta}
        for path, total in sorted(updated.items(), key=lambda item: len(item[0].parts), reverse=True):
            limit = self._limit(path)
            if total < DIRECTORY_CHARGE or total > _MAX_TOTAL:
                raise ResultAccountingError('storage_error', 'Result accounting subtotal is invalid.')
            if enforce and total > self.total(path) and limit is not None and total > limit:
                raise self._capacity_error(path, limit)
        self._begin_change()
        self._totals.update(updated)
        self._changed.update(updated)

    @_mutation
    def mkdir(self, path, *, enforce=True):
        """Reserve a directory's fixed accounting overhead before creating it."""
        path = self._data(path)
        if path.exists() or path.is_symlink():
            self._safe(path, directory=True)
            return
        self._safe(path.parent, directory=True)
        limit = self._limit(path)
        if enforce and limit is not None and DIRECTORY_CHARGE > limit:
            raise self._capacity_error(path, limit)
        self._adjust({parent: DIRECTORY_CHARGE for parent in self._ancestry(path.parent)}, enforce=enforce)
        create_dir(path)
        self._sync(path.parent)
        self._totals[path] = DIRECTORY_CHARGE
        self._changed.add(path)

    @_mutation
    def write(self, path, chunks, limit, *, enforce=True, representation_scope=None):
        """Separate an optional representation bound from charged scope capacity.

        Every flushed buffer reserves actual scope bytes before writing. A file
        format limit never reports cache exhaustion; partial failures leave the
        transaction dirty until recovery.
        """
        path = self._data(path)
        self._safe(path.parent, directory=True)
        ancestry = self._ancestry(path.parent)
        for parent in ancestry:
            self.total(parent)
        self._begin_change()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        written = reserved = 0
        try:
            with os.fdopen(fd, 'wb') as stream:
                def flush(data):
                    nonlocal written, reserved
                    self._adjust({parent: len(data) for parent in ancestry}, enforce=enforce)
                    reserved += len(data)
                    if stream.write(data) != len(data):
                        raise OSError('Incomplete result write')
                    written += len(data)

                buffer = bytearray()
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ResultAccountingError('storage_error', 'Result storage requires encoded byte chunks.')
                    if limit is not None and written + len(buffer) + len(chunk) > limit:
                        raise ResultAccountingError('representation_too_large',
                            'The result page or metadata exceeds its representation limit.',
                            representation_scope=representation_scope, actual_limit=limit)
                    if len(buffer) + len(chunk) > 65536:
                        if buffer:
                            flush(buffer)
                            buffer.clear()
                        if len(chunk) >= 65536:
                            flush(chunk)
                            continue
                    buffer.extend(chunk)
                if buffer:
                    flush(buffer)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync(path.parent)
        except BaseException:
            path.unlink(missing_ok=True)
            self._sync(path.parent)
            self._adjust({parent: -reserved for parent in ancestry}, enforce=False)
            raise
        return written

    @_mutation
    def replace(self, source, target, *, enforce=True):
        """Transfer existing charges across scopes and release overwritten file bytes."""
        source = self._data(source)
        target = self._data(target)
        source_info = source.lstat()
        directory = stat.S_ISDIR(source_info.st_mode)
        self._safe(source, directory=directory)
        if source == target:
            return
        self._safe(target.parent, directory=True)
        amount = self.total(source) if directory else source_info.st_size
        old = 0
        if target.exists() or target.is_symlink():
            old = self._safe(target).st_size
        changes = {}
        for path in self._ancestry(source.parent):
            changes[path] = changes.get(path, 0) - amount
        for path in self._ancestry(target.parent):
            changes[path] = changes.get(path, 0) + amount - old
        if directory:
            limit = self._limit(target)
            if enforce and limit is not None and amount > limit:
                raise self._capacity_error(target, limit)
        self._adjust(changes, enforce=enforce)
        os.replace(source, target)
        self._sync(source.parent)
        if source.parent != target.parent:
            self._sync(target.parent)
        if directory:
            moved = [(path, total) for path, total in self._totals.items() if path.is_relative_to(source)]
            for path, total in moved:
                changed = path in self._changed
                self._totals.pop(path)
                self._changed.discard(path)
                renamed = target / path.relative_to(source)
                self._totals[renamed] = total
                if changed:
                    self._changed.add(renamed)

    @_mutation
    def remove(self, path):
        """Delete only the requested subtree; release capacity after durable deletion."""
        path = self._data(path)
        if path == self.root:
            raise ResultAccountingError('unsafe_path', 'The result accounting root cannot be removed.')
        if not path.exists() and not path.is_symlink():
            return
        info = path.lstat()
        directory = stat.S_ISDIR(info.st_mode)
        self._safe(path, directory=directory)
        amount = self.total(path) if directory else info.st_size
        if directory:
            def fail(error):
                raise error
            for root, dirs, files in os.walk(path, followlinks=False, onerror=fail):
                for name in dirs:
                    self._safe(Path(root) / name, directory=True)
                for name in files:
                    self._safe(Path(root) / name)
        self._begin_change()
        if directory:
            shutil.rmtree(path)
        else:
            path.unlink()
        self._sync(path.parent)
        self._adjust({parent: -amount for parent in self._ancestry(path.parent)}, enforce=False)
        for cached in list(self._totals):
            if cached.is_relative_to(path):
                self._totals.pop(cached)
                self._changed.discard(cached)
