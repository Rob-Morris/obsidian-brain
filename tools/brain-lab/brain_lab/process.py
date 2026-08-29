from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Sequence


DEFAULT_STREAM_LIMIT = 8 * 1024 * 1024
THREAD_JOIN_GRACE_SECONDS = 1
THREAD_CLEANUP_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class StreamReceipt:
    path: str
    retained_bytes: int
    total_bytes: int
    sha256: str
    truncated: bool


@dataclass(frozen=True)
class ProcessExecution:
    argv: tuple[str, ...]
    returncode: int | None
    timed_out: bool
    cancelled: bool
    duration_seconds: float
    stdout: StreamReceipt
    stderr: StreamReceipt
    stdin_error: str | None = None
    stream_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled and self.stdin_error is None

    @property
    def evidence_complete(self) -> bool:
        return (
            not self.stdout.truncated
            and not self.stderr.truncated
            and self.stdin_error is None
            and self.stream_error is None
        )

    def to_dict(self) -> dict:
        return asdict(self)


class _BoundedSink:
    def __init__(self, path: Path, limit: int):
        self._path = path
        self._limit = limit
        self._retained = 0
        self._total = 0
        self._digest = hashlib.sha256()

    def drain(self, source: BinaryIO) -> None:
        with self._path.open("wb") as destination:
            while chunk := source.read(64 * 1024):
                self._total += len(chunk)
                self._digest.update(chunk)
                remaining = max(0, self._limit - self._retained)
                if remaining:
                    kept = chunk[:remaining]
                    destination.write(kept)
                    self._retained += len(kept)

    def receipt(self) -> StreamReceipt:
        return StreamReceipt(
            path=str(self._path),
            retained_bytes=self._retained,
            total_bytes=self._total,
            sha256=self._digest.hexdigest(),
            truncated=self._retained < self._total,
        )


class CommandRunner:
    def __init__(self, *, stream_limit: int = DEFAULT_STREAM_LIMIT):
        if stream_limit <= 0:
            raise ValueError("stream_limit must be greater than zero")
        self.stream_limit = stream_limit

    def run(
        self,
        argv: Sequence[str],
        *,
        evidence_directory: Path,
        timeout_seconds: float = 300,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        stdin_writer: Callable[[BinaryIO], None] | None = None,
    ) -> ProcessExecution:
        if not argv or stdin is not None and stdin_writer is not None:
            raise ValueError("argv is required and stdin must have exactly one source")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        evidence_directory.mkdir(parents=True, exist_ok=True)
        stdout_sink = _BoundedSink(evidence_directory / "stdout.log", self.stream_limit)
        stderr_sink = _BoundedSink(evidence_directory / "stderr.log", self.stream_limit)
        child_environment = None
        if environment is not None:
            child_environment = os.environ.copy()
            child_environment.update(environment)
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=child_environment,
            stdin=subprocess.PIPE if stdin is not None or stdin_writer is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        stream_errors: list[str] = []

        def drain(sink: _BoundedSink, source: BinaryIO) -> None:
            try:
                sink.drain(source)
            except (OSError, ValueError) as exc:
                stream_errors.append(f"{type(exc).__name__}: {exc}")

        readers = [
            threading.Thread(target=drain, args=(stdout_sink, process.stdout), daemon=True),
            threading.Thread(target=drain, args=(stderr_sink, process.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        stdin_errors: list[str] = []

        def write_stdin() -> None:
            assert process.stdin is not None
            try:
                if stdin_writer is not None:
                    stdin_writer(process.stdin)
                elif stdin is not None:
                    process.stdin.write(stdin)
            except Exception as exc:
                stdin_errors.append(str(exc))
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        writer = None
        if process.stdin is not None:
            writer = threading.Thread(target=write_stdin, daemon=True)
            writer.start()

        started = time.monotonic()
        timed_out = False
        cancelled = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_group(process)
        except KeyboardInterrupt:
            cancelled = True
            self._terminate_process_group(process)
        self._join_threads(readers, THREAD_JOIN_GRACE_SECONDS)
        if any(reader.is_alive() for reader in readers):
            self._terminate_lingering_process_group(process.pid)
            self._join_threads(readers, THREAD_CLEANUP_TIMEOUT_SECONDS)
        if any(reader.is_alive() for reader in readers):
            raise RuntimeError("command stream readers did not terminate after process-group cleanup")
        if writer is not None:
            writer.join(timeout=THREAD_JOIN_GRACE_SECONDS)
            if writer.is_alive():
                self._terminate_lingering_process_group(process.pid)
                writer.join(timeout=THREAD_CLEANUP_TIMEOUT_SECONDS)
            if writer.is_alive():
                raise RuntimeError("command stdin writer did not terminate after process completion")
        return ProcessExecution(
            argv=tuple(argv),
            returncode=process.returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            duration_seconds=round(time.monotonic() - started, 6),
            stdout=stdout_sink.receipt(),
            stderr=stderr_sink.receipt(),
            stdin_error=stdin_errors[0] if stdin_errors else None,
            stream_error=stream_errors[0] if stream_errors else None,
        )

    @staticmethod
    def _join_threads(threads: Sequence[threading.Thread], timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))

    @staticmethod
    def _terminate_lingering_process_group(process_group_id: int) -> None:
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            return
        time.sleep(1)
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    @staticmethod
    def interactive(argv: Sequence[str], *, environment: Mapping[str, str] | None = None) -> int:
        child_environment = None
        if environment is not None:
            child_environment = os.environ.copy()
            child_environment.update(environment)
        return subprocess.call(list(argv), env=child_environment)
