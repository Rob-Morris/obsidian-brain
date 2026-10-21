"""Source delegation preserves destination inputs without ambient machine selection."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from _bootstrap import machine_cli

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from _local_cli import main


def complete_source(tmp_path):
    source = tmp_path / "source"
    marker = source / "cli/_launcher/approval_lifecycle.py"
    marker.parent.mkdir(parents=True)
    marker.touch()
    return source


@pytest.mark.parametrize("selection", [None, {}, {
    "BRAIN_VAULT_ROOT": "selected-vault", "BRAIN_WORKSPACE_DIR": "selected-workspace",
    "PYTHONPATH": "must-not-import",
}])
def test_only_explicit_handoff_preserves_destination_environment(tmp_path, monkeypatch, selection):
    source = complete_source(tmp_path)
    monkeypatch.setenv("BRAIN_VAULT_ROOT", "ambient-vault")
    monkeypatch.setenv("BRAIN_WORKSPACE_DIR", "ambient-workspace")
    monkeypatch.setenv("PYTHONPATH", "unrelated-code")
    captured = {}
    def launch(argv, **options):
        captured.update(options)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(machine_cli, "run_managed", launch)
    machine_cli.run_source_cli(source, tmp_path / "brain", ["upgrade"],
                              selection_environment=selection)
    for key in ("BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR"):
        assert captured["env"].get(key) == (selection or {}).get(key)
    assert captured["env"]["PYTHONPATH"].split(machine_cli.os.pathsep) == [
        str(source / "cli"), str(source / "src/brain-core/scripts")]
    assert captured["env"]["PYTHONIOENCODING"] == captured["encoding"] == "utf-8"
    assert captured["text"] is True


@pytest.mark.parametrize("explicit", [None, "vault", "brain_id", "workspace"])
def test_upgrade_handoff_retains_environment_and_explicit_selector_precedence(
        tmp_path, monkeypatch, capsys, explicit):
    source = complete_source(tmp_path)
    monkeypatch.setenv("BRAIN_VAULT_ROOT", "intended-vault")
    monkeypatch.setenv("BRAIN_WORKSPACE_DIR", "intended-workspace")
    common = SimpleNamespace(vault=None, brain_id=None, workspace=None, operator_key=None,
                             dry_run=True, json_mode=True)
    if explicit:
        setattr(common, explicit, "explicit-selection")
    envelope = {"schema": "brain.command-result/1", "command": "brain.upgrade",
                "command_version": 1, "status": "ok", "warnings": [],
                "result": {}, "committed_effects": []}
    captured = {}
    def launch(argv, **options):
        captured.update(argv=argv, **options)
        return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")
    monkeypatch.setattr(machine_cli, "run_managed", launch)
    assert main._run_selected_lifecycle(["upgrade"], common,
        {"source_root": str(source), "version": None}, tmp_path / "brain",
        tmp_path / "running-source", None) == 0
    assert captured["env"]["BRAIN_VAULT_ROOT"] == "intended-vault"
    assert captured["env"]["BRAIN_WORKSPACE_DIR"] == "intended-workspace"
    for name, flag in (("vault", "--vault"), ("brain_id", "--brain"), ("workspace", "--workspace")):
        if name == explicit:
            assert captured["argv"][captured["argv"].index(flag) + 1] == "explicit-selection"
        else:
            assert flag not in captured["argv"]
    assert json.loads(capsys.readouterr().out) == envelope
