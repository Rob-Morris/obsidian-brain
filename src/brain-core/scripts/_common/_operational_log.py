"""Always-on, bounded, content-free operational logging (``brain.operational-log/1``).

Three layers over the fixed ``.brain/local/diagnostics/`` destination:

- Record layer: pure encoding, identifier sanitisation and error classification.
- File layer: locked batch appends with rotation, plus export and clear.
  Invariant: a log file is never open outside its family lock, so several
  processes can append to one family and rotation stays safe on POSIX
  (no stale-handle inode drift) and possible at all on Windows (renames
  fail while any process holds the file open).
- Delivery layer: one bounded-queue daemon logger per long-lived process,
  and a synchronous append for one-shot processes.

Logging is best-effort by contract: nothing in this module may unwind a
caller's request. This module stays stdlib-only and imports nothing above
``_common`` so every tier — the CLI launcher included — can use it.
"""

from __future__ import annotations

import atexit
import errno
import itertools
import json
import os
import queue
import re
import stat
import sys
import threading
import time
import uuid
from pathlib import Path

from ._file_lock import MutationLockError, exclusive_file_lock


SCHEMA = "brain.operational-log/1"
BODIES_SCHEMA = "brain.debug-bodies/1"
EXPORT_SCHEMA = "brain.diagnostics-export/1"

DIAGNOSTICS_REL = Path(".brain") / "local" / "diagnostics"
FAMILIES = ("command", "debug-bodies", "proxy", "proxy-rpc", "server")
PROCESSES = ("cli", "proxy", "script", "server")
_PRIMARY_FAMILY = {"cli": "command", "proxy": "proxy", "script": "command", "server": "server"}

MAX_FILE_BYTES = 2 * 1024 * 1024
ARCHIVE_COUNT = 3
MAX_RECORD_BYTES = 4 * 1024
MAX_RAW_LINE_BYTES = 64 * 1024
QUEUE_CAPACITY = 1024
MAX_DRAIN_BATCH = 128
LOCK_TIMEOUT = 2.0
CLOSE_TIMEOUT = 1.0
MAX_IDENTIFIER_LENGTH = 96
_MAX_CONSECUTIVE_WRITE_FAILURES = 5
_IDENTIFIER_EXTRA = frozenset(".-_")
_COMMAND_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_EXCEPTION_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")
_RPC_METHODS = frozenset(
    {
        "completion.complete",
        "elicitation.create",
        "initialize",
        "logging.setLevel",
        "notifications.cancelled",
        "notifications.initialized",
        "notifications.progress",
        "notifications.resources.list_changed",
        "notifications.resources.updated",
        "notifications.roots.list_changed",
        "notifications.tools.list_changed",
        "ping",
        "prompts.get",
        "prompts.list",
        "resources.list",
        "resources.read",
        "resources.subscribe",
        "resources.templates.list",
        "resources.unsubscribe",
        "roots.list",
        "sampling.createMessage",
        "tools.call",
        "tools.list",
    }
)
_PHASES = frozenset(
    {
        "authority.before-resolution",
        "authority.preflight",
        "catalogue.resolve",
        "direct-script.invoke",
        "execute",
        "preflight",
        "receipt.finalise",
        "receipt.preflight",
    }
)
_REPLAY_REASONS = frozenset(
    {
        "accepted_call_invalid",
        "accepted_call_missing",
        "command_identity_changed",
        "command_version_changed",
        "indeterminate_interface_change",
        "interface_epoch_changed",
        "mutation_class_changed",
        "projected_tool_removed",
        "proxy_protocol_incompatible",
        "replacement_header_invalid",
    }
)
_RESOLUTION_SOURCES = frozenset(
    {
        "registry_default",
        "vault_root_env",
        "vault_self",
        "workspace_binding",
        "workspace_env",
    }
)


def _core_version() -> str:
    try:
        return (
            (Path(__file__).resolve().parents[2] / "VERSION")
            .read_text(encoding="utf-8")
            .strip()
            or "unknown"
        )
    except OSError:
        return "unknown"


_VERSION = _core_version()


# ---------------------------------------------------------------------------
# Record layer — pure functions, no I/O
# ---------------------------------------------------------------------------

def sanitise_identifier(value: object) -> str:
    """Reduce one identifier to the content-free allowlist, degrading to 'invalid'."""
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH:
        return "invalid"
    for char in value:
        if not (char.isascii() and (char.isalnum() or char in _IDENTIFIER_EXTRA)):
            return "invalid"
    return value


def normalise_rpc_method(value: object) -> str:
    """Project an untrusted JSON-RPC method into the closed diagnostics vocabulary."""
    if not isinstance(value, str):
        return "other"
    projected = value.replace("/", ".")
    return projected if projected in _RPC_METHODS else "other"


def _closed_identifier(value: object) -> str:
    result = sanitise_identifier(value)
    if result == "invalid":
        raise ValueError("diagnostics identifier is outside the closed grammar")
    return result


def _command_id(value: object) -> str:
    if not isinstance(value, str) or not _COMMAND_ID_RE.fullmatch(value):
        raise ValueError("diagnostics command_id is outside the command grammar")
    return value


def _exception_type(value: object) -> str:
    if not isinstance(value, str) or not _EXCEPTION_TYPE_RE.fullmatch(value):
        raise ValueError("diagnostics exception_type is outside the class-name grammar")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("diagnostics field must be an integer")
    return value


def _nonnegative_integer(value: object) -> int:
    result = _integer(value)
    if result < 0:
        raise ValueError("diagnostics field must be non-negative")
    return result


def _nonnegative_number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("diagnostics field must be a non-negative number")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("diagnostics field must be a boolean")
    return value


def _one_of(allowed: frozenset[str]):
    def validate(value: object) -> str:
        if not isinstance(value, str) or value not in allowed:
            raise ValueError("diagnostics field is outside its closed vocabulary")
        return value

    return validate


_EVENT_FIELDS = {
    "child.exited": {"exit_code": (_integer, True)},
    "child.restart_scheduled": {
        "backoff_slot": (_nonnegative_integer, True),
        "delay_s": (_nonnegative_number, True),
    },
    "child.spawned": {"child_pid": (_nonnegative_integer, True)},
    "command.failed": {
        "phase": (_one_of(_PHASES), True),
        "command_id": (_command_id, True),
        "correlation_id": (_closed_identifier, True),
        "error_class": (
            _one_of(frozenset({"capacity", "internal", "io", "lock", "transport"})),
            True,
        ),
        "exception_type": (_exception_type, True),
    },
    "frame.completed": {
        "frame_seq": (_nonnegative_integer, True),
        "duration_ms": (_nonnegative_integer, False),
        "outcome": (_one_of(frozenset({"error", "ok"})), True),
    },
    "frame.forwarded": {
        "frame_seq": (_nonnegative_integer, True),
        "method": (_one_of(_RPC_METHODS | {"other"}), True),
        "replayed": (_boolean, False),
    },
    "interface.accepted": {"interface_epoch": (_nonnegative_integer, True)},
    "interface.rejected": {},
    "process.exited": {"exit_code": (_integer, False)},
    "process.started": {
        "bodies_enabled": (_boolean, False),
        "resolution_source": (_one_of(_RESOLUTION_SOURCES), False),
    },
    "replay.refused": {"reason": (_one_of(_REPLAY_REASONS), True)},
    "tool.handled": {
        "command_id": (_command_id, True),
        "duration_ms": (_nonnegative_integer, True),
        "error_class": (
            _one_of(frozenset({"capacity", "internal", "io", "lock", "transport"})),
            False,
        ),
        "invocation_id": (_closed_identifier, False),
        "outcome": (_one_of(frozenset({"error", "ok"})), True),
    },
    "tool.started": {
        "command_id": (_command_id, True),
        "invocation_id": (_closed_identifier, True),
    },
}


def _validated_event_fields(event: object, fields: dict[str, object]) -> tuple[str, dict[str, object]]:
    if not isinstance(event, str) or event not in _EVENT_FIELDS:
        raise ValueError("unknown operational diagnostics event")
    schema = _EVENT_FIELDS[event]
    unknown = sorted(set(fields) - set(schema))
    if unknown:
        raise ValueError(f"{event}: unknown diagnostics fields: {', '.join(unknown)}")
    validated: dict[str, object] = {}
    for name, (validator, required) in schema.items():
        value = fields.get(name)
        if value is None:
            if required:
                raise ValueError(f"{event}: missing diagnostics field {name}")
            continue
        validated[name] = validator(value)
    return event, validated


def classify_error(error: BaseException) -> str:
    """Map one exception to the closed error-class vocabulary. Never raises."""
    if isinstance(error, MutationLockError):
        return "lock"
    if isinstance(error, (BrokenPipeError, ConnectionError, TimeoutError)):
        return "transport"
    if isinstance(error, (MemoryError, RecursionError, queue.Full)):
        return "capacity"
    if isinstance(error, OSError):
        return "io"
    return "internal"


def encode_record(
    *,
    process: str,
    run_id: str,
    seq: int,
    event: str,
    dropped_before: int = 0,
    ts_ms: int | None = None,
    **fields: object,
) -> bytes:
    """Encode one operational record as a single NDJSON line of bytes.

    Field values are ints, floats, bools, or allowlist-sanitised identifier
    strings — there is no free-form message channel. Oversize records are
    replaced by a ``log.record_truncated`` marker rather than dropped.
    """
    if process not in PROCESSES:
        raise ValueError(f"unknown diagnostics process: {process}")
    event, validated_fields = _validated_event_fields(event, fields)
    record: dict[str, object] = {
        "schema": SCHEMA,
        "ts": int(time.time() * 1000) if ts_ms is None else int(ts_ms),
        "run_id": _closed_identifier(run_id),
        "seq": int(seq),
        "process": process,
        "pid": os.getpid(),
        "version": _VERSION,
        "event": event,
    }
    if dropped_before > 0:
        record["dropped_before"] = int(dropped_before)
    record.update(sorted(validated_fields.items()))
    encoded = _encode_line(record)
    if len(encoded) <= MAX_RECORD_BYTES:
        return encoded
    marker = dict(record)
    marker = {
        key: marker[key]
        for key in ("schema", "ts", "run_id", "seq", "process", "pid", "version")
    }
    marker["event"] = "log.record_truncated"
    marker["original_event"] = record["event"]
    marker["size"] = len(encoded)
    if dropped_before > 0:
        marker["dropped_before"] = int(dropped_before)
    return _encode_line(marker)


def encode_bodies_line(
    *,
    run_id: str,
    direction: str,
    method: object,
    body: str,
    ts_ms: int | None = None,
) -> bytes:
    """Encode one opt-in wire-body capture line (``brain.debug-bodies/1``).

    Unlike operational records this deliberately carries content; it is only
    ever written to the ``debug-bodies`` family. Oversize bodies are truncated
    in place with a ``truncated: true`` flag so one giant document cannot
    balloon the shared queue.
    """
    record: dict[str, object] = {
        "schema": BODIES_SCHEMA,
        "ts": int(time.time() * 1000) if ts_ms is None else int(ts_ms),
        "run_id": sanitise_identifier(run_id),
        "pid": os.getpid(),
        "direction": sanitise_identifier(direction),
        "method": sanitise_identifier(method),
        "body": body,
    }
    encoded = _encode_line(record)
    if len(encoded) <= MAX_RAW_LINE_BYTES:
        return encoded
    overhead = len(encoded) - len(record["body"])
    record["body"] = body[: max(0, MAX_RAW_LINE_BYTES - overhead - 64)]
    record["truncated"] = True
    return _encode_line(record)


def _encode_line(record: dict[str, object]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        + b"\n"
    )


# ---------------------------------------------------------------------------
# File layer — locked appends, rotation, export, clear
# ---------------------------------------------------------------------------

def diagnostics_directory(vault_root: Path) -> Path:
    """Return the fixed diagnostics directory for one vault (not created)."""
    return Path(vault_root) / DIAGNOSTICS_REL


def _ensure_directory(vault_root: Path) -> Path:
    """Create the diagnostics directory 0700, refusing symlinked components."""
    current = Path(vault_root)
    for part in DIAGNOSTICS_REL.parts:
        current = current / part
        if current.is_symlink():
            raise OSError(f"refusing symlinked diagnostics component: {current}")
        if not current.exists():
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                # Another process created the component between check and mkdir.
                pass
        if not current.is_dir() or current.is_symlink():
            raise OSError(f"diagnostics component is not a regular directory: {current}")
    os.chmod(current, 0o700)
    return current


def _family_lock(directory: Path, family: str):
    return exclusive_file_lock(
        directory / f"{family}.lock",
        timeout=LOCK_TIMEOUT,
        follow_symlinks=False,
    )


def _require_family(family: str) -> None:
    if family not in FAMILIES:
        raise ValueError(f"unknown diagnostics family: {family}")


def _open_regular_file(path: Path, flags: int) -> int:
    """Open one diagnostics endpoint without following its final component."""
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        opened = os.fstat(descriptor)
        endpoint = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(endpoint.st_mode)
            or (endpoint.st_dev, endpoint.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError(f"diagnostics endpoint is not a regular file: {path}")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _regular_existing_path(path: Path) -> bool:
    """Return false for a missing path and reject every non-regular endpoint."""
    try:
        endpoint = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(endpoint.st_mode):
        raise OSError(f"diagnostics endpoint is not a regular file: {path}")
    return True


def append_lines(vault_root: Path, family: str, lines: list[bytes]) -> None:
    """Append pre-encoded lines under the family lock, rotating before a write
    that would push the active file past ``MAX_FILE_BYTES``."""
    _require_family(family)
    if not lines:
        return
    directory = _ensure_directory(vault_root)
    active = directory / f"{family}.log"
    with _family_lock(directory, family):
        descriptor = _open_regular_file(active, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            size = os.fstat(descriptor).st_size
            for line in lines:
                if size > 0 and size + len(line) > MAX_FILE_BYTES:
                    os.close(descriptor)
                    descriptor = -1
                    _rotate(directory, family)
                    descriptor = _open_regular_file(
                        active, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                    )
                    size = 0
                os.write(descriptor, line)
                size += len(line)
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _rotate(directory: Path, family: str) -> None:
    oldest = directory / f"{family}.log.{ARCHIVE_COUNT}"
    if _regular_existing_path(oldest):
        oldest.unlink()
    for index in range(ARCHIVE_COUNT - 1, 0, -1):
        source = directory / f"{family}.log.{index}"
        if _regular_existing_path(source):
            os.replace(source, directory / f"{family}.log.{index + 1}")
    active = directory / f"{family}.log"
    if _regular_existing_path(active):
        os.replace(active, directory / f"{family}.log.1")


def _family_files_chronological(directory: Path, family: str) -> list[Path]:
    ordered = [
        directory / f"{family}.log.{index}"
        for index in range(ARCHIVE_COUNT, 0, -1)
    ]
    ordered.append(directory / f"{family}.log")
    return [path for path in ordered if _regular_existing_path(path)]


def export_logs(vault_root: Path, destination_dir: Path) -> Path:
    """Concatenate every retained log chronologically into one new export file.

    A family whose lock cannot be acquired within the timeout is skipped with
    an inline section note — export never fails wholesale over one busy family.
    """
    from ._filesystem import safe_write_via

    directory = diagnostics_directory(vault_root)
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    output = destination_dir / f"brain-diagnostics-{int(time.time() * 1000)}.log"

    def _write(handle) -> None:
        handle.write(
            _encode_line(
                {
                    "schema": EXPORT_SCHEMA,
                    "exported_at_ms": int(time.time() * 1000),
                    "vault_version": _VERSION,
                }
            )
        )
        for family in FAMILIES:
            if not directory.is_dir():
                continue
            try:
                with _family_lock(directory, family):
                    for path in _family_files_chronological(directory, family):
                        handle.write(f"===== {path.name} =====\n".encode("ascii"))
                        descriptor = _open_regular_file(path, os.O_RDONLY)
                        try:
                            while chunk := os.read(descriptor, 65536):
                                handle.write(chunk)
                        finally:
                            os.close(descriptor)
            except MutationLockError:
                handle.write(
                    f"===== {family} skipped: lock busy =====\n".encode("ascii")
                )

    safe_write_via(output, _write, mode="wb", exclusive=True)
    os.chmod(output, 0o600)
    return output


def clear_logs(vault_root: Path) -> int:
    """Truncate every active log and delete archives. Returns files touched."""
    directory = diagnostics_directory(vault_root)
    if not directory.is_dir():
        return 0
    touched = 0
    for family in FAMILIES:
        with _family_lock(directory, family):
            active = directory / f"{family}.log"
            if _regular_existing_path(active):
                descriptor = _open_regular_file(active, os.O_RDWR)
                try:
                    os.ftruncate(descriptor, 0)
                finally:
                    os.close(descriptor)
                touched += 1
            for index in range(1, ARCHIVE_COUNT + 1):
                archive = directory / f"{family}.log.{index}"
                if _regular_existing_path(archive):
                    archive.unlink()
                    touched += 1
    return touched


# ---------------------------------------------------------------------------
# Delivery layer — daemon logger and one-shot append
# ---------------------------------------------------------------------------

_FINAL = "final"
_RAW = "raw"
_RECORD = "record"


class OperationalLogger:
    """One bounded queue and writer thread serving all of a process's families.

    ``record`` never blocks a request thread: a full queue counts a drop that
    the next accepted record reports in-band as ``dropped_before``.
    """

    def __init__(self, vault_root: Path, process: str) -> None:
        if process not in PROCESSES:
            raise ValueError(f"unknown diagnostics process: {process}")
        self.vault_root = Path(vault_root)
        self.process = process
        self.run_id = uuid.uuid4().hex[:12]
        self._primary_family = _PRIMARY_FAMILY[process]
        self._seq = itertools.count()
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_CAPACITY)
        self._drop_lock = threading.Lock()
        self._dropped = 0
        self._accepting = True
        self._abandoned = False
        self._disabled = False
        self._closed = False
        self._consecutive_failures = 0
        self._writer = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name=f"{process}-diagnostics",
        )
        self._writer.start()

    # -- producers ---------------------------------------------------------

    def record(self, event: str, *, family: str | None = None, **fields: object) -> None:
        """Enqueue one operational record. Non-blocking; never raises."""
        try:
            resolved = family or self._primary_family
            _require_family(resolved)
            self._enqueue((_RECORD, resolved, (event, fields, int(time.time() * 1000))))
        except Exception:
            self._add_drops(1)

    def record_raw(self, family: str, line: bytes) -> None:
        """Enqueue one pre-encoded line (bodies capture). Never raises."""
        try:
            _require_family(family)
            if len(line) > MAX_RAW_LINE_BYTES:
                line = _encode_line(
                    {"schema": BODIES_SCHEMA, "truncated": True, "size": len(line)}
                )
            self._enqueue((_RAW, family, line))
        except Exception:
            self._add_drops(1)

    def close(self, timeout: float = CLOSE_TIMEOUT, *, exit_code: int | None = None) -> None:
        """Stop accepting, flush, and write the final ``process.exited`` record.

        Bounded: past the timeout the writer is abandoned (it no-ops before
        every further lock acquisition and write) rather than joined forever.
        """
        if self._closed:
            return
        self._closed = True
        self._accepting = False
        deadline = time.monotonic() + timeout
        final = (_FINAL, self._primary_family, (exit_code, int(time.time() * 1000)))
        while True:
            try:
                self._queue.put_nowait(final)
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    self._abandoned = True
                    return
                time.sleep(0.005)
        self._writer.join(max(0.0, deadline - time.monotonic()))
        if self._writer.is_alive():
            self._abandoned = True

    def _enqueue(self, item: tuple) -> None:
        if not self._accepting:
            return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._add_drops(1)

    def _add_drops(self, count: int) -> None:
        with self._drop_lock:
            self._dropped += count

    def _take_drops(self) -> int:
        with self._drop_lock:
            dropped = self._dropped
            self._dropped = 0
            return dropped

    # -- writer thread ------------------------------------------------------

    def _writer_loop(self) -> None:
        try:
            while True:
                batch = [self._queue.get()]
                for _ in range(MAX_DRAIN_BATCH - 1):
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                finished = self._write_batch(batch)
                if finished:
                    return
        except Exception as error:  # the writer must never take the daemon down
            self._disabled = True
            _stderr_note(f"operational log writer stopped: {error!r}")

    def _write_batch(self, batch: list[tuple]) -> bool:
        finished = False
        by_family: dict[str, list[bytes]] = {}
        pending = 0
        for item in batch:
            kind, family, payload = item
            if kind == _RAW:
                by_family.setdefault(family, []).append(payload)
                pending += 1
                continue
            if kind == _RECORD:
                event, fields, ts_ms = payload
                line = encode_record(
                    process=self.process,
                    run_id=self.run_id,
                    seq=next(self._seq),
                    event=event,
                    dropped_before=self._take_drops(),
                    ts_ms=ts_ms,
                    **fields,
                )
                by_family.setdefault(family, []).append(line)
                pending += 1
                continue
            if kind == _FINAL:
                finished = True
                exit_code, ts_ms = payload
                fields = {} if exit_code is None else {"exit_code": exit_code}
                line = encode_record(
                    process=self.process,
                    run_id=self.run_id,
                    seq=next(self._seq),
                    event="process.exited",
                    dropped_before=self._take_drops(),
                    ts_ms=ts_ms,
                    **fields,
                )
                by_family.setdefault(family, []).append(line)
                pending += 1
        if self._abandoned or self._disabled:
            return finished
        try:
            for family in sorted(by_family):
                append_lines(self.vault_root, family, by_family[family])
            self._consecutive_failures = 0
        except (OSError, MutationLockError) as error:
            self._add_drops(pending)
            self._consecutive_failures += 1
            persistent = isinstance(error, OSError) and error.errno in (
                errno.EACCES,
                errno.ENOSPC,
            )
            if persistent or self._consecutive_failures >= _MAX_CONSECUTIVE_WRITE_FAILURES:
                self._disabled = True
                _stderr_note(f"operational log disabled after write failure: {error}")
        return finished


_INSTALLED: OperationalLogger | None = None
_ONESHOT_LOCK = threading.Lock()
_ONESHOT_RUN_ID: str | None = None
_ONESHOT_SEQ = itertools.count()


def install(vault_root: Path, process: str) -> OperationalLogger:
    """Install the process-wide daemon logger exactly once."""
    global _INSTALLED
    if _INSTALLED is not None:
        raise RuntimeError("operational logger is already installed in this process")
    logger = OperationalLogger(vault_root, process)
    _INSTALLED = logger
    atexit.register(logger.close)
    return logger


def current_logger() -> OperationalLogger | None:
    """Return the installed daemon logger, or None in one-shot processes."""
    return _INSTALLED


def append_record(vault_root: Path, process: str, event: str, **fields: object) -> None:
    """Synchronously append one record to the ``command`` family. Never raises."""
    try:
        global _ONESHOT_RUN_ID
        with _ONESHOT_LOCK:
            if _ONESHOT_RUN_ID is None:
                _ONESHOT_RUN_ID = uuid.uuid4().hex[:12]
            run_id = _ONESHOT_RUN_ID
            seq = next(_ONESHOT_SEQ)
        line = encode_record(
            process=process,
            run_id=run_id,
            seq=seq,
            event=event,
            **fields,
        )
        append_lines(Path(vault_root), "command", [line])
    except Exception as error:
        _stderr_note(f"operational log append failed: {error}")


def _stderr_note(message: str) -> None:
    try:
        print(f"[brain-diagnostics] {message}", file=sys.stderr)
    except Exception:
        pass
