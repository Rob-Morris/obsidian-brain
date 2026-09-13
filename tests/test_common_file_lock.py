from pathlib import Path
import os
import subprocess
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"))

from _common import _file_lock  # noqa: E402


def test_exclusive_file_lock_uses_win32_msvcrt_branch(monkeypatch, tmp_path):
    calls: list[tuple[int, int]] = []
    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=0,
        locking=lambda fd, mode, size: calls.append((mode, size)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(_file_lock.sys, "platform", "win32")

    with _file_lock.exclusive_file_lock(tmp_path / "registry.lock"):
        assert calls == [(fake_msvcrt.LK_LOCK, 1)]

    assert calls == [(fake_msvcrt.LK_LOCK, 1), (fake_msvcrt.LK_UNLCK, 1)]
    assert (tmp_path / "registry.lock").stat().st_size == 1


def test_exclusive_file_lock_preserves_existing_win32_lock_byte(monkeypatch, tmp_path):
    lock_path = tmp_path / "registry.lock"
    lock_path.write_bytes(b"existing")
    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=0,
        locking=lambda _fd, _mode, _size: None,
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(_file_lock.sys, "platform", "win32")

    with _file_lock.exclusive_file_lock(lock_path):
        pass

    assert lock_path.read_bytes() == b"existing"


def test_exclusive_file_lock_wraps_win32_acquire_failure(monkeypatch, tmp_path):
    def fail_lock(_fd, mode, _size):
        if mode == fake_msvcrt.LK_LOCK:
            raise OSError("busy")

    fake_msvcrt = types.SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=0,
        locking=fail_lock,
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(_file_lock.sys, "platform", "win32")
    lock_path = tmp_path / "registry.lock"

    with pytest.raises(RuntimeError, match=f"could not acquire exclusive lock on {lock_path}"):
        with _file_lock.exclusive_file_lock(lock_path):
            pass


def test_exclusive_file_lock_creates_parent_directory(tmp_path):
    lock_path = tmp_path / "nested" / "registry.lock"

    with _file_lock.exclusive_file_lock(lock_path):
        assert lock_path.exists()


def test_public_mutation_error_message_passes_through_non_lock_errors():
    assert _file_lock.public_mutation_error_message(ValueError("invalid")) == "invalid"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX subprocess blocking contract")
def test_exclusive_file_lock_blocks_by_default(tmp_path):
    lock_path = tmp_path / "registry.lock"
    scripts_dir = str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts")
    code = (
        "from _common._file_lock import exclusive_file_lock; "
        f"p={str(lock_path)!r}; "
        "\nwith exclusive_file_lock(p): pass"
    )
    env = dict(os.environ, PYTHONPATH=scripts_dir)

    with _file_lock.exclusive_file_lock(lock_path):
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.1)
        except BaseException:
            process.kill()
            process.wait()
            raise

    stdout, stderr = process.communicate(timeout=2)
    assert process.returncode == 0, (stdout, stderr)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX subprocess contention contract")
def test_exclusive_file_lock_reports_cross_process_contention(tmp_path):
    lock_path = tmp_path / "mutation.lock"
    scripts_dir = str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts")
    code = (
        "from _common._file_lock import exclusive_file_lock; "
        f"p={str(lock_path)!r}; "
        "\nwith exclusive_file_lock(p, timeout=0.1): pass"
    )
    env = dict(os.environ, PYTHONPATH=scripts_dir)

    with _file_lock.exclusive_file_lock(lock_path):
        completed = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )

    assert completed.returncode != 0
    assert "timed out after 0.1s acquiring exclusive lock" in completed.stderr
    assert f"pid={os.getpid()}" in completed.stderr


def test_noncreating_lock_never_restores_removed_parent(tmp_path):
    lock_path = tmp_path / "ended-owner" / "pins.lock"
    with pytest.raises(FileNotFoundError):
        with _file_lock.exclusive_file_lock(lock_path, create_parent=False):
            pytest.fail("absent owner must not be entered")
    assert not lock_path.parent.exists()


def test_compatibility_exports_preserve_canonical_lock_identity():
    from _bootstrap import file_lock

    assert _file_lock.exclusive_file_lock is file_lock.exclusive_file_lock
    assert _file_lock.MutationLockError is file_lock.MutationLockError
