from __future__ import annotations

import subprocess
from pathlib import Path

from brain_lab.host_state import capture_host_state


def test_host_state_detects_machine_config_and_vault_changes(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "brain"
    config.mkdir(parents=True)
    (config / "vaults").write_text("brain\tlocal\t/vault\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("one", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    before = capture_host_state(home=home, vaults=[vault])
    (config / "brains.json").write_text('{"version":1,"brains":[]}\n', encoding="utf-8")
    (vault / "note.md").write_text("two", encoding="utf-8")
    after = capture_host_state(home=home, vaults=[vault])

    assert before["fingerprint"] != after["fingerprint"]
    assert before["files"]["brain_machine_config"] != after["files"]["brain_machine_config"]


def test_host_state_git_fingerprint_includes_dirty_state(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "file").write_text("one", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    before = capture_host_state(home=tmp_path / "home", worktrees=[repository])
    (repository / "file").write_text("two", encoding="utf-8")
    after = capture_host_state(home=tmp_path / "home", worktrees=[repository])

    assert before["fingerprint"] != after["fingerprint"]
