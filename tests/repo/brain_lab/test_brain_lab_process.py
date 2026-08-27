from __future__ import annotations

import io
import signal
import subprocess
import sys
import time
from pathlib import Path

from brain_lab.process import CommandRunner


def test_command_runner_drains_but_bounds_both_streams(tmp_path: Path):
    runner = CommandRunner(stream_limit=32)
    execution = runner.run(
        [
            sys.executable,
            "-c",
            "import sys; print('x' * 100); print('y' * 100, file=sys.stderr)",
        ],
        evidence_directory=tmp_path / "evidence",
        timeout_seconds=5,
    )

    assert execution.succeeded
    assert execution.stdout.total_bytes > execution.stdout.retained_bytes == 32
    assert execution.stderr.total_bytes > execution.stderr.retained_bytes == 32
    assert execution.stdout.truncated and execution.stderr.truncated
    assert not execution.evidence_complete


def test_command_runner_terminates_process_group_on_timeout(tmp_path: Path):
    runner = CommandRunner()
    execution = runner.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        evidence_directory=tmp_path / "evidence",
        timeout_seconds=0.05,
    )

    assert execution.timed_out
    assert not execution.succeeded
    assert execution.returncode is not None


def test_command_runner_terminates_descendant_holding_streams_after_parent_exits(
    tmp_path: Path,
):
    started = time.monotonic()
    execution = CommandRunner().run(
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys; "
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                "print('parent complete')"
            ),
        ],
        evidence_directory=tmp_path / "evidence",
        timeout_seconds=5,
    )

    assert execution.succeeded
    assert time.monotonic() - started < 4
    assert (tmp_path / "evidence" / "stdout.log").read_text() == "parent complete\n"


def test_command_runner_classifies_keyboard_interrupt_as_cancellation(
    tmp_path: Path, monkeypatch
):
    class InterruptedProcess:
        pid = 12345
        stdin = None

        def __init__(self):
            self.stdout = io.BytesIO(b"partial output")
            self.stderr = io.BytesIO(b"")
            self.returncode = None
            self.wait_count = 0

        def wait(self, timeout=None):
            self.wait_count += 1
            if self.wait_count == 1:
                raise KeyboardInterrupt
            self.returncode = -signal.SIGTERM
            return self.returncode

        def poll(self):
            return self.returncode

    process = InterruptedProcess()
    signals = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("brain_lab.process.os.killpg", lambda pid, sig: signals.append((pid, sig)))

    execution = CommandRunner().run(
        ["fake-command"],
        evidence_directory=tmp_path / "evidence",
        timeout_seconds=5,
    )

    assert execution.cancelled
    assert not execution.succeeded
    assert execution.stdout.total_bytes == len(b"partial output")
    assert signals == [(12345, signal.SIGTERM)]
