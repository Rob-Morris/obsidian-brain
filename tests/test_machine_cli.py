"""Direct script delegation finds the installed CLI on each native platform."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from _bootstrap import machine_cli


@pytest.mark.parametrize(("platform", "local_app_data", "relative_binary"), [
    ("darwin", False, ".local/bin/brain"),
    ("win32", False, ".local/bin/brain.cmd"),
    ("win32", True, "AppData/Local/Programs/Brain/bin/brain.cmd"),
])
def test_fallback_matches_native_installer(tmp_path, monkeypatch, platform, local_app_data, relative_binary):
    monkeypatch.setattr(machine_cli.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(machine_cli.shutil, "which", lambda name: None)
    if local_app_data:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData/Local"))
    else:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    binary = tmp_path / relative_binary
    binary.parent.mkdir(parents=True)
    binary.write_text("disposable launcher fixture")
    calls = []
    receipt = {"schema": "brain.command-result/1", "status": "ok"}

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt), stderr="")

    monkeypatch.setattr(machine_cli.subprocess, "run", run)
    assert machine_cli.invoke("brain.register", {"vault_root": str(tmp_path / "vault")}) == receipt
    assert calls[0][:5] == [str(binary), "command", "describe", "approvals.configure", "--json"]
    assert calls[1][:2] == [str(binary), "register"]
