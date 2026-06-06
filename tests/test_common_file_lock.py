from pathlib import Path
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
