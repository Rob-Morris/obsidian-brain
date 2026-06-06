from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"))

from _common import _shell  # noqa: E402


def test_join_argv_matches_shlex_join_on_posix(monkeypatch):
    argv = ["python3.12", "repair.py", "runtime", "--vault", "/path/with spaces/Brain"]
    monkeypatch.setattr(_shell.sys, "platform", "linux")

    assert _shell.join_argv(argv) == shlex.join(argv)


def test_quote_arg_matches_shlex_quote_on_posix(monkeypatch):
    value = "/path/with spaces/Brain"
    monkeypatch.setattr(_shell.sys, "platform", "linux")

    assert _shell.quote_arg(value) == shlex.quote(value)


def test_join_argv_uses_list2cmdline_on_win32(monkeypatch):
    argv = ["python", "repair.py", "runtime", "--vault", r"C:\Users\Rob Morris\Brain"]
    monkeypatch.setattr(_shell.sys, "platform", "win32")

    assert _shell.join_argv(argv) == subprocess.list2cmdline(argv)
    assert '"C:\\Users\\Rob Morris\\Brain"' in _shell.join_argv(argv)


def test_quote_arg_uses_list2cmdline_on_win32(monkeypatch):
    value = r"C:\Users\Rob Morris\Brain"
    monkeypatch.setattr(_shell.sys, "platform", "win32")

    assert _shell.quote_arg(value) == subprocess.list2cmdline([value])
    assert _shell.quote_arg(value) == '"C:\\Users\\Rob Morris\\Brain"'
