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

    monkeypatch.setattr("_common._venv.subprocess.run", run)
    assert machine_cli.invoke("brain.register", {"vault_root": str(tmp_path / "vault")}) == receipt
    assert calls[0][:5] == [str(binary), "command", "describe", "approvals.configure", "--json"]
    assert calls[1][:2] == [str(binary), "register"]


def test_selected_source_delegates_bootstrap_without_importing_foreign_policy(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli"))
    import _distribution
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(machine_cli.shutil, "which", lambda name: None)
    source = tmp_path / "source"
    owner = source / "cli/_launcher/approval_lifecycle.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# complete-source marker\n")
    calls = []
    monkeypatch.setattr(_distribution, "ensure_cli", lambda root, binary: calls.append((root, binary)))
    receipt = {"schema": "brain.command-result/1", "status": "ok"}
    subprocess_calls = []
    def run(argv, **kwargs):
        subprocess_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt), stderr="")
    monkeypatch.setattr("_common._venv.subprocess.run", run)
    assert machine_cli.invoke("brain.install", {}, source_root=source) == receipt
    assert calls == [], "bootstrap belongs to the isolated selected-source child"
    argv = subprocess_calls[-1]
    assert json.loads(argv[argv.index("--request-json") + 1])["source_root"] == str(source)


def test_source_execution_forwards_existing_owner_and_process_identity(tmp_path, monkeypatch):
    from _bootstrap.owner_attachment import PROCESS_CONTEXT_ENV
    source = tmp_path / "source"
    owner = source / "cli/_launcher/approval_lifecycle.py"
    owner.parent.mkdir(parents=True)
    owner.touch()
    process_context = '{"schema":"brain.process-context/1","kind":"cli-job","context_id":"existing","initialise_owner":false}'
    monkeypatch.setenv(PROCESS_CONTEXT_ENV, process_context)
    monkeypatch.setenv("PYTHONPATH", "/unrelated/source")
    captured = {}

    class Attachment:
        def forwarded_process(self, env):
            forwarded = dict(env)
            forwarded.pop(PROCESS_CONTEXT_ENV, None)
            return {"env": forwarded, "pass_fds": (17,), "close_fds": True}

    def run(argv, **options):
        captured.update(argv=argv, **options)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(machine_cli, "run_managed", run)
    binary = tmp_path / "actual-brain"
    machine_cli.run_source_cli(source, binary, ["upgrade"], owner_attachment=Attachment())
    assert captured["pass_fds"] == (17,)
    assert captured["env"][PROCESS_CONTEXT_ENV] == process_context
    assert captured["env"]["BRAIN_CLI_BINARY"] == str(binary)
    assert captured["env"][machine_cli.LIFECYCLE_SOURCE_ENV] == str(source)
    assert captured["env"]["PYTHONPATH"].split(machine_cli.os.pathsep) == [
        str(source / "cli"), str(source / "src/brain-core/scripts"),
    ]


def test_non_install_machine_command_does_not_bootstrap_an_absent_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(machine_cli.shutil, 'which', lambda _name: None)
    source = tmp_path / 'source'
    source.mkdir()
    with pytest.raises(ValueError, match='Install the Brain CLI explicitly'):
        machine_cli.invoke('registry.add', {'vault_root': str(tmp_path / 'vault')}, source_root=source)
    assert not (tmp_path / '.local').exists()
