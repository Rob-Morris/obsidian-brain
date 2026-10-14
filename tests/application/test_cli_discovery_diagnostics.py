"""Safe diagnostic evidence at the selected-Brain discovery process boundary."""

from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from _local_cli import main
from _local_cli.runtime import SelectedBrain


def test_unbound_ordinary_command_reports_actual_child_failure(command_vault_clone, tmp_path, capsys):
    workspace = tmp_path / "unbound-workspace"
    workspace.mkdir()
    selected = SelectedBrain(command_vault_clone.vault_root, workspace, "explicit_vault")
    with pytest.raises(main.CliError) as failure:
        main._invoke_application(
            selected, "runtime.status", {},
            SimpleNamespace(operator_key=None), dependency_tier="bootstrap",
        )
    message = str(failure.value)
    assert "invalid JSON (exit 4)" in message
    assert "already bound to the selected Brain" in message
    assert "brain workspace register --brain" in message
    assert str(workspace) not in message
    assert not (workspace / ".brain").exists()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("code,stdout,stderr,expected", [
    (4, "secret-key", "command.py: infrastructure — direct workspace cannot be resolved: secret-key", "could not resolve the workspace binding"),
    (1, "secret-key", "Traceback\nsecret-key\n" * 3000, "check the selected Brain command entry point and runtime"),
    (127, "secret-key", "secret-key", "structural process contract"),
    (0, "secret-key", "", "invalid JSON"),
    (0, '{"secret-key":true}', "secret-key", "schema is unsupported"),
])
def test_malformed_child_output_is_bounded_and_does_not_echo_streams(monkeypatch, tmp_path, capsys, code, stdout, stderr, expected):
    completed = SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(main, "managed_command", lambda *args, **kwargs: SimpleNamespace(run=lambda **options: completed))
    with pytest.raises(main.CliError) as failure:
        main._invoke_application(
            SelectedBrain(tmp_path, None, "test"), "command.describe", {},
            SimpleNamespace(operator_key="secret-key"), dependency_tier="bootstrap",
        )
    message = str(failure.value)
    assert f"exit {code}" in message
    assert expected in message
    assert "secret-key" not in message
    assert len(message.encode()) < 512
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("code", [0, 4])
def test_valid_discovery_envelope_is_preserved_despite_stderr(monkeypatch, tmp_path, capsys, code):
    envelope = {"schema": "brain.command-result/1", "command": "command.describe",
                "command_version": 1, "status": "ok", "result": {"unchanged": True}}
    if code == 4:
        envelope.update(status="error", error={"code": "internal_error", "message": "failed"})
    completed = SimpleNamespace(returncode=code, stdout=json.dumps(envelope), stderr="secret-key")
    monkeypatch.setattr(main, "managed_command", lambda *args, **kwargs: SimpleNamespace(run=lambda **options: completed))
    assert main._invoke_application(
        SelectedBrain(tmp_path, None, "test"), "command.describe", {},
        SimpleNamespace(operator_key=None), dependency_tier="bootstrap",
    ) == envelope
    assert capsys.readouterr() == ("", "")


def test_legacy_bound_use_returns_structured_pinned_upgrade_without_writes(command_vault_clone, tmp_path):
    import vault_registry

    root = command_vault_clone.vault_root
    vault_registry.register(root, "schema-brain")
    workspace = tmp_path / "folder with spaces"
    manifest = workspace / ".brain/local/workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("brain: schema-brain\nslug: old-local-name\nlinks:\n  workspace: canonical-key\n")
    original = manifest.read_bytes()
    result = main._invoke_application(SelectedBrain(root, workspace, "explicit_vault"),
        "runtime.status", {}, SimpleNamespace(operator_key=None), dependency_tier="bootstrap")
    assert result["status"] == "error"
    assert result["error"]["code"] == "conflict"
    details = result["error"]["details"]
    assert details["reason"] == "upgrade_required"
    assert details["folder"] == str(workspace)
    assert details["detected_version"] is None
    assert details["supported_version"] == 1
    import shlex
    assert shlex.split(details["remediation_command"]) == [
        "brain", "workspace", "upgrade", "--brain", "schema-brain", "--workspace", str(workspace)]
    assert manifest.read_bytes() == original

    from command_application import application_for
    from _application.session.start import SessionStartRequest
    from _application.types import DependencyTier

    from test_session_start_owner import _mark_ready

    _mark_ready(root)
    bootstrap = application_for(root, workspace_dir=workspace, dependency_tier=DependencyTier.MANAGED).invoke(SessionStartRequest())
    assert bootstrap.status == "ok", bootstrap.error.message if bootstrap.status == "error" else bootstrap
    content = bootstrap.result.content
    cursor = bootstrap.result.range.next_cursor
    while cursor is not None:
        page = application_for(root, workspace_dir=workspace, dependency_tier=DependencyTier.MANAGED).invoke(SessionStartRequest(cursor=cursor))
        assert page.status == "ok", page
        content += page.result.content
        cursor = page.result.range.next_cursor
    assert details["remediation_command"] in content
    assert "upgrade_required" in content
    assert manifest.read_bytes() == original
