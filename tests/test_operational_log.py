"""Contract tests for the operational diagnostics log (`_common._operational_log`)."""

from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"))

from _common import _operational_log as oplog  # noqa: E402
from _common._file_lock import MutationLockError, exclusive_file_lock  # noqa: E402


FORBIDDEN_RECORD_KEYS = {"message", "request", "path", "body", "arguments", "params"}
POSIX = sys.platform != "win32"


def _vault(tmp_path: Path) -> Path:
    (tmp_path / ".brain-core").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".brain-core" / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    return tmp_path


def _lines(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _make_logger(tmp_path: Path, process: str = "server") -> oplog.OperationalLogger:
    return oplog.OperationalLogger(_vault(tmp_path), process)


# ---------------------------------------------------------------------------
# Record layer
# ---------------------------------------------------------------------------

def test_sanitise_identifier_accepts_allowlisted_identifiers():
    assert oplog.sanitise_identifier("artefact.create") == "artefact.create"
    assert oplog.sanitise_identifier("mcp-1234_ok") == "mcp-1234_ok"


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        42,
        "x" * 97,
        "has space",
        "vault/path",
        "naïve",
        "line\nbreak",
        b"bytes",
        {"nested": "object"},
    ],
)
def test_sanitise_identifier_degrades_to_invalid_without_raising(value):
    assert oplog.sanitise_identifier(value) == "invalid"


def test_classify_error_covers_the_closed_vocabulary():
    assert oplog.classify_error(MutationLockError("busy")) == "lock"
    assert oplog.classify_error(BrokenPipeError()) == "transport"
    assert oplog.classify_error(ConnectionResetError()) == "transport"
    assert oplog.classify_error(TimeoutError()) == "transport"
    assert oplog.classify_error(MemoryError()) == "capacity"
    assert oplog.classify_error(queue.Full()) == "capacity"
    assert oplog.classify_error(PermissionError()) == "io"
    assert oplog.classify_error(FileNotFoundError()) == "io"
    assert oplog.classify_error(ValueError("boom")) == "internal"
    assert oplog.classify_error(RuntimeError("boom")) == "internal"


def test_encode_record_is_structured_and_content_free():
    line = oplog.encode_record(
        process="server",
        run_id="abc123",
        seq=7,
        event="tool.handled",
        command_id="artefact.create",
        invocation_id="direct-1234",
        duration_ms=12,
        outcome="ok",
    )
    record = json.loads(line)
    assert record["schema"] == "brain.operational-log/1"
    assert record["event"] == "tool.handled"
    assert record["seq"] == 7
    assert record["pid"] == os.getpid()
    assert record["command_id"] == "artefact.create"
    assert record["duration_ms"] == 12
    assert record["outcome"] == "ok"
    assert "dropped_before" not in record
    assert not FORBIDDEN_RECORD_KEYS & set(record)


def test_encode_record_rejects_unknown_fields_and_untrusted_rpc_methods():
    with pytest.raises(ValueError, match="unknown diagnostics fields"):
        oplog.encode_record(
            process="server",
            run_id="abc123",
            seq=7,
            event="command.failed",
            command_id="artefact.create",
            correlation_id="direct-1234",
            error_class="internal",
            exception_type="ValueError",
            free_text="PatientSSN123",
        )

    assert oplog.normalise_rpc_method("tools/call") == "tools.call"
    assert oplog.normalise_rpc_method("PatientSSN123") == "other"
    assert oplog.normalise_rpc_method({"method": "tools/call"}) == "other"


def test_encode_record_reports_drops_in_band():
    record = json.loads(
        oplog.encode_record(
            process="proxy",
            run_id="r",
            seq=1,
            event="frame.forwarded",
            dropped_before=3,
            frame_seq=1,
            method="tools.call",
        )
    )
    assert record["dropped_before"] == 3


def test_encode_record_replaces_oversize_records_with_a_marker(monkeypatch):
    fields = {f"f{i}": (oplog._nonnegative_integer, True) for i in range(600)}
    monkeypatch.setitem(oplog._EVENT_FIELDS, "test.oversize", fields)
    line = oplog.encode_record(
        process="server",
        run_id="r",
        seq=1,
        event="test.oversize",
        **{f"f{i}": 10**9 for i in range(600)},
    )
    assert len(line) <= oplog.MAX_RECORD_BYTES
    record = json.loads(line)
    assert record["event"] == "log.record_truncated"
    assert record["original_event"] == "test.oversize"
    assert record["size"] > oplog.MAX_RECORD_BYTES


def test_encode_bodies_line_truncates_oversize_bodies_in_place():
    line = oplog.encode_bodies_line(
        run_id="r",
        direction="client_to_child",
        method="tools/call",
        body="x" * (oplog.MAX_RAW_LINE_BYTES + 100),
    )
    assert len(line) <= oplog.MAX_RAW_LINE_BYTES
    record = json.loads(line)
    assert record["truncated"] is True
    assert record["schema"] == "brain.debug-bodies/1"
    # "tools/call" fails the identifier allowlist deliberately — the method
    # field stays content-free even inside the bodies capture envelope.
    assert record["method"] == "invalid"


# ---------------------------------------------------------------------------
# File layer
# ---------------------------------------------------------------------------

def test_append_lines_creates_private_directory_and_files(tmp_path):
    vault = _vault(tmp_path)
    oplog.append_lines(vault, "command", [b'{"schema":"x"}\n'])
    directory = oplog.diagnostics_directory(vault)
    assert directory.is_dir()
    active = directory / "command.log"
    assert active.is_file()
    if POSIX:
        assert oct(directory.stat().st_mode & 0o777) == "0o700"
        assert oct(active.stat().st_mode & 0o777) == "0o600"
        assert oct((directory / "command.lock").stat().st_mode & 0o777) == "0o600"


def test_append_lines_rejects_unknown_family(tmp_path):
    with pytest.raises(ValueError):
        oplog.append_lines(_vault(tmp_path), "nope", [b"x\n"])


def test_append_lines_refuses_symlinked_diagnostics_directory(tmp_path):
    vault = _vault(tmp_path)
    local = vault / ".brain" / "local"
    local.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (local / "diagnostics").symlink_to(elsewhere)
    with pytest.raises(OSError):
        oplog.append_lines(vault, "command", [b"x\n"])


@pytest.mark.skipif(not POSIX, reason="endpoint no-follow semantics")
@pytest.mark.parametrize("endpoint", ("command.lock", "command.log"))
def test_append_lines_refuses_symlinked_file_endpoints(tmp_path, endpoint):
    vault = _vault(tmp_path)
    directory = oplog._ensure_directory(vault)
    external = tmp_path / "external"
    external.write_bytes(b"outside\n")
    (directory / endpoint).symlink_to(external)

    with pytest.raises(OSError):
        oplog.append_lines(vault, "command", [b"must-not-escape\n"])

    assert external.read_bytes() == b"outside\n"


@pytest.mark.skipif(not POSIX, reason="endpoint no-follow semantics")
@pytest.mark.parametrize("operation", ("export", "clear"))
def test_read_and_clear_refuse_symlinked_log_endpoints(tmp_path, operation):
    vault = _vault(tmp_path)
    directory = oplog._ensure_directory(vault)
    external = tmp_path / "external"
    external.write_bytes(b"outside\n")
    (directory / "server.log").symlink_to(external)

    with pytest.raises(OSError):
        if operation == "export":
            oplog.export_logs(vault, tmp_path / "out")
        else:
            oplog.clear_logs(vault)

    assert external.read_bytes() == b"outside\n"


def test_rotation_triggers_before_the_write_that_would_cross_the_cap(tmp_path):
    vault = _vault(tmp_path)
    line = b"x" * 1023 + b"\n"
    per_file = oplog.MAX_FILE_BYTES // len(line)
    oplog.append_lines(vault, "server", [line] * per_file)
    active = oplog.diagnostics_directory(vault) / "server.log"
    assert active.stat().st_size == per_file * len(line) <= oplog.MAX_FILE_BYTES
    oplog.append_lines(vault, "server", [line])
    assert active.stat().st_size == len(line)
    archive = oplog.diagnostics_directory(vault) / "server.log.1"
    assert archive.stat().st_size == per_file * len(line)


def test_rotation_retains_only_the_fixed_archive_count(tmp_path):
    vault = _vault(tmp_path)
    directory = oplog.diagnostics_directory(vault)
    line = b"y" * (oplog.MAX_FILE_BYTES // 2) + b"\n"
    for _ in range(12):
        oplog.append_lines(vault, "proxy", [line])
    files = sorted(path.name for path in directory.glob("proxy.log*") if path.suffix != ".lock")
    assert files == ["proxy.log", "proxy.log.1", "proxy.log.2", "proxy.log.3"]
    total = sum(
        (directory / name).stat().st_size for name in files
    )
    assert total <= oplog.MAX_FILE_BYTES * (oplog.ARCHIVE_COUNT + 1)


def test_multi_process_appends_stay_line_atomic(tmp_path):
    vault = _vault(tmp_path)
    scripts_dir = str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);\n"
        "from pathlib import Path\n"
        "from _common import _operational_log as oplog\n"
        "for i in range(50):\n"
        "    oplog.append_lines(Path(sys.argv[2]), 'command', "
        "[oplog.encode_record(process='script', run_id=sys.argv[3], seq=i, "
        "event='command.failed', phase='execute', command_id='artefact.read', "
        "correlation_id='direct-worker', error_class='internal', "
        "exception_type='ValueError')])\n"
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", code, scripts_dir, str(vault), f"worker{n}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for n in range(3)
    ]
    for worker in workers:
        _stdout, stderr = worker.communicate(timeout=60)
        assert worker.returncode == 0, stderr
    records = _lines(oplog.diagnostics_directory(vault) / "command.log")
    assert len(records) == 150
    assert {record["run_id"] for record in records} == {"worker0", "worker1", "worker2"}


def test_clear_logs_truncates_active_and_deletes_archives(tmp_path):
    vault = _vault(tmp_path)
    directory = oplog.diagnostics_directory(vault)
    line = b"z" * (oplog.MAX_FILE_BYTES // 2) + b"\n"
    for _ in range(6):
        oplog.append_lines(vault, "server", [line])
    assert (directory / "server.log.1").exists()
    touched = oplog.clear_logs(vault)
    assert touched >= 2
    assert (directory / "server.log").stat().st_size == 0
    assert not (directory / "server.log.1").exists()
    oplog.append_lines(vault, "server", [b"after\n"])
    assert (directory / "server.log").read_bytes() == b"after\n"


def test_clear_logs_races_safely_with_concurrent_appends(tmp_path):
    vault = _vault(tmp_path)
    errors: list[BaseException] = []
    stop = threading.Event()

    def _appender() -> None:
        line = b"a" * 4095 + b"\n"
        while not stop.is_set():
            try:
                oplog.append_lines(vault, "command", [line] * 8)
            except BaseException as error:  # noqa: BLE001 — the test asserts none occur
                errors.append(error)
                return

    thread = threading.Thread(target=_appender)
    thread.start()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        oplog.clear_logs(vault)
    stop.set()
    thread.join(timeout=10)
    assert errors == []
    oplog.append_lines(vault, "command", [b"post-clear\n"])
    content = (oplog.diagnostics_directory(vault) / "command.log").read_bytes()
    assert content.endswith(b"post-clear\n")


@pytest.mark.skipif(not POSIX, reason="flock contention semantics")
def test_export_skips_a_family_whose_lock_is_held(tmp_path):
    vault = _vault(tmp_path)
    oplog.append_lines(vault, "server", [b'{"held":"no"}\n'])
    oplog.append_lines(vault, "command", [b'{"held":"maybe"}\n'])
    directory = oplog.diagnostics_directory(vault)
    started = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with exclusive_file_lock(directory / "command.lock"):
            started.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=_hold)
    holder.start()
    try:
        assert started.wait(timeout=10)
        output = oplog.export_logs(vault, tmp_path / "out")
    finally:
        release.set()
        holder.join(timeout=10)
    text = output.read_text(encoding="utf-8")
    header = json.loads(text.splitlines()[0])
    assert header["schema"] == "brain.diagnostics-export/1"
    assert "===== command skipped: lock busy =====" in text
    assert "===== server.log =====" in text
    assert '{"held":"no"}' in text
    if POSIX:
        assert oct(output.stat().st_mode & 0o777) == "0o600"


def test_export_concatenates_families_chronologically(tmp_path):
    vault = _vault(tmp_path)
    line = b"c" * (oplog.MAX_FILE_BYTES // 2) + b"\n"
    oplog.append_lines(vault, "server", [line, line, line])
    oplog.append_lines(vault, "server", [b"newest\n"])
    output = oplog.export_logs(vault, tmp_path / "out")
    text = output.read_text(encoding="utf-8")
    assert text.index("===== server.log.1 =====") < text.index("===== server.log =====")
    assert text.rstrip().endswith("newest")


# ---------------------------------------------------------------------------
# Delivery layer
# ---------------------------------------------------------------------------

def test_daemon_logger_routes_families_and_defaults_to_primary(tmp_path):
    logger = _make_logger(tmp_path, process="proxy")
    logger.record("child.spawned", child_pid=123)
    logger.record("frame.forwarded", family="proxy-rpc", frame_seq=1, method="tools.call")
    logger.record_raw(
        "debug-bodies",
        oplog.encode_bodies_line(
            run_id=logger.run_id, direction="client_to_child", method="m", body="{}"
        ),
    )
    logger.close(exit_code=0)
    directory = oplog.diagnostics_directory(tmp_path)
    proxy_records = _lines(directory / "proxy.log")
    assert [record["event"] for record in proxy_records] == [
        "child.spawned",
        "process.exited",
    ]
    assert proxy_records[-1]["exit_code"] == 0
    rpc_records = _lines(directory / "proxy-rpc.log")
    assert rpc_records[0]["event"] == "frame.forwarded"
    assert rpc_records[0]["frame_seq"] == 1
    bodies = _lines(directory / "debug-bodies.log")
    assert bodies[0]["schema"] == "brain.debug-bodies/1"


def test_no_record_is_accepted_after_close(tmp_path):
    logger = _make_logger(tmp_path)
    logger.record("tool.started", command_id="artefact.create", invocation_id="direct-1")
    logger.close(exit_code=0)
    logger.record(
        "tool.handled",
        command_id="artefact.create",
        invocation_id="direct-1",
        duration_ms=1,
        outcome="ok",
    )
    logger.close(exit_code=0)
    records = _lines(oplog.diagnostics_directory(tmp_path) / "server.log")
    assert [record["event"] for record in records] == ["tool.started", "process.exited"]
    sequences = [record["seq"] for record in records]
    assert sequences == sorted(sequences)


def test_queue_overflow_drops_are_reported_on_the_next_accepted_record(tmp_path):
    logger = _make_logger(tmp_path)
    fields = {
        "command_id": "artefact.read",
        "duration_ms": 1,
        "outcome": "ok",
    }
    logger._add_drops(4)
    logger._write_batch([("record", "server", ("tool.handled", fields, 0))])
    records = _lines(oplog.diagnostics_directory(tmp_path) / "server.log")
    assert records[-1]["dropped_before"] == 4
    logger._write_batch([("record", "server", ("tool.handled", fields, 0))])
    records = _lines(oplog.diagnostics_directory(tmp_path) / "server.log")
    assert "dropped_before" not in records[-1]
    logger.close()


def test_full_queue_counts_a_drop_instead_of_blocking(tmp_path):
    logger = _make_logger(tmp_path)
    stuck: queue.Queue = queue.Queue(maxsize=1)
    stuck.put_nowait(("record", "server", ("sentinel", {}, 0)))
    logger._queue = stuck
    started = time.monotonic()
    logger.record("tool.started", command_id="artefact.read", invocation_id="direct-1")
    assert time.monotonic() - started < 0.5
    assert logger._take_drops() == 1


def test_close_is_bounded_when_the_writer_is_saturated(tmp_path):
    logger = _make_logger(tmp_path)
    stuck: queue.Queue = queue.Queue(maxsize=1)
    stuck.put_nowait(("record", "server", ("sentinel", {}, 0)))
    logger._queue = stuck
    started = time.monotonic()
    logger.close(timeout=0.2)
    assert time.monotonic() - started < 1.0
    assert logger._abandoned is True


def test_lock_failure_drops_the_batch_and_recovers_on_the_next(tmp_path, monkeypatch):
    logger = _make_logger(tmp_path)
    calls = {"count": 0}
    real_append = oplog.append_lines

    def _flaky(vault_root, family, lines):
        calls["count"] += 1
        if calls["count"] == 1:
            raise MutationLockError("busy")
        real_append(vault_root, family, lines)

    monkeypatch.setattr(oplog, "append_lines", _flaky)
    started = {"command_id": "artefact.read", "invocation_id": "direct-1"}
    handled = {"command_id": "artefact.read", "duration_ms": 1, "outcome": "ok"}
    logger._write_batch([("record", "server", ("tool.started", started, 0))])
    assert logger._disabled is False
    logger._write_batch([("record", "server", ("tool.handled", handled, 0))])
    records = _lines(oplog.diagnostics_directory(tmp_path) / "server.log")
    assert records[-1]["event"] == "tool.handled"
    assert records[-1]["dropped_before"] == 1
    logger.close()


def test_persistent_write_failure_disables_the_writer(tmp_path, monkeypatch, capsys):
    logger = _make_logger(tmp_path)

    def _denied(_vault_root, _family, _lines):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(oplog, "append_lines", _denied)
    logger._write_batch(
        [
            (
                "record",
                "server",
                (
                    "tool.started",
                    {"command_id": "artefact.read", "invocation_id": "direct-1"},
                    0,
                ),
            )
        ]
    )
    assert logger._disabled is True
    assert "operational log disabled" in capsys.readouterr().err
    logger.close()


def test_all_file_writes_happen_on_the_writer_thread(tmp_path, monkeypatch):
    writer_threads: set[str] = set()
    real_append = oplog.append_lines

    def _spy(vault_root, family, lines):
        writer_threads.add(threading.current_thread().name)
        real_append(vault_root, family, lines)

    monkeypatch.setattr(oplog, "append_lines", _spy)
    logger = _make_logger(tmp_path, process="server")
    for index in range(20):
        logger.record(
            "tool.handled",
            command_id="artefact.read",
            duration_ms=index,
            outcome="ok",
        )
    logger.close(exit_code=0)
    assert writer_threads == {"server-diagnostics"}


def test_writer_drain_batches_remain_bounded_when_producers_keep_queue_nonempty():
    logger = object.__new__(oplog.OperationalLogger)
    logger._queue = queue.Queue()
    for index in range(oplog.MAX_DRAIN_BATCH * 2 + 7):
        logger._queue.put_nowait(("raw", "server", f"{index}\n".encode()))
    logger._queue.put_nowait(("final", "server", (0, 0)))
    batches = []

    def _write(batch):
        batches.append(batch)
        return any(item[0] == "final" for item in batch)

    logger._write_batch = _write
    logger._disabled = False
    logger._writer_loop()

    assert [len(batch) for batch in batches] == [
        oplog.MAX_DRAIN_BATCH,
        oplog.MAX_DRAIN_BATCH,
        8,
    ]


def test_install_publishes_current_logger_once(tmp_path, monkeypatch):
    monkeypatch.setattr(oplog, "_INSTALLED", None)
    assert oplog.current_logger() is None
    logger = oplog.install(_vault(tmp_path), "server")
    try:
        assert oplog.current_logger() is logger
        with pytest.raises(RuntimeError):
            oplog.install(_vault(tmp_path), "server")
    finally:
        logger.close(exit_code=0)


def test_append_record_works_without_an_installed_logger(tmp_path, monkeypatch):
    monkeypatch.setattr(oplog, "_INSTALLED", None)
    vault = _vault(tmp_path)
    oplog.append_record(
        vault,
        "script",
        "command.failed",
        phase="execute",
        command_id="artefact.create",
        correlation_id="direct-abc",
        error_class="internal",
        exception_type="ValueError",
    )
    records = _lines(oplog.diagnostics_directory(vault) / "command.log")
    assert records[0]["event"] == "command.failed"
    assert records[0]["correlation_id"] == "direct-abc"
    assert records[0]["error_class"] == "internal"
    assert not FORBIDDEN_RECORD_KEYS & set(records[0])


def test_append_record_never_raises(tmp_path, capsys):
    vault = _vault(tmp_path)
    (vault / ".brain" / "local").mkdir(parents=True)
    (vault / ".brain" / "local" / "diagnostics").symlink_to(tmp_path / "elsewhere")
    oplog.append_record(vault, "script", "command.failed")
    assert "operational log append failed" in capsys.readouterr().err
