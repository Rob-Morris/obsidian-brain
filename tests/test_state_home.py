"""The machine state home resolves the same way for every reader of it.

``_bootstrap.paths.state_home`` serves the upgrade rollback journal and the
launcher's own state, so a relative ``$XDG_STATE_HOME`` is ignored like a
relative ``$XDG_CONFIG_HOME`` is, and ``$HOME`` is honoured before
``Path.home()``.
"""

from pathlib import Path

from _bootstrap.paths import state_home


def test_state_home_is_the_isolated_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    assert state_home() == tmp_path / "state"


def test_a_relative_xdg_state_home_is_ignored_and_home_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert state_home() == tmp_path / ".local" / "state"


def test_an_empty_xdg_state_home_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert state_home() == tmp_path / ".local" / "state"
    assert state_home() != Path.cwd(), "an empty value never resolves to the working directory"


def test_the_launcher_resolves_the_same_state_home(monkeypatch, tmp_path):
    from _launcher.context import launcher_state_home

    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert launcher_state_home() == state_home() == tmp_path / ".local" / "state"
