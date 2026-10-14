"""Real CLI selection, discovery and binding for a previously unbound folder."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import yaml
import vault_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))
from _local_cli.main import run, _resolve
from _local_cli.runtime import resolve_folder_brain, resolve_selected_brain
from _bootstrap.workspace_binding import save_workspace_manifest_data, read_workspace_manifest
from _bootstrap.workspace_binding import WorkspaceBindingError
from brain_test_support import register_other_brain


@pytest.mark.parametrize("selector", ["default", "vault", "brain"])
def test_cli_register_discovers_requires_consent_and_then_binds(command_vault_clone, tmp_path, monkeypatch, capsys, selector):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "new repo"
    workspace.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(repo_root / "cli/brain"))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(repo_root))
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    vault_registry.register(root, "specific-brain")
    vault_registry.set_default("specific-brain")
    selection = ([] if selector == "default" else
                 ["--vault", str(root)] if selector == "vault" else ["--brain", "specific-brain"])
    argv = [*selection, "--workspace", str(workspace), "workspace", "register",
            "--request-json", '{"slug":"canonical"}', "--json"]

    denied = run(argv)
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    assert denied == 3, output
    assert envelope["error"]["code"] == "authority_denied", envelope
    assert envelope["error"]["details"]["boundary"] == "context", envelope
    assert envelope.get("committed_effects", []) == []
    assert not (workspace / ".brain").exists()

    config_path = root / ".brain/config.yaml"
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    config.setdefault("defaults", {}).setdefault("access", {}).setdefault("overrides", {})["workspace.register"] = True
    config_path.write_text(yaml.safe_dump(config))
    from _application.workspace.create import WorkspaceCreateRequest
    from command_application import application_for
    created = application_for(root).invoke(WorkspaceCreateRequest("canonical"))
    assert created.status == "ok", created
    assert run(argv) == 0
    output = capsys.readouterr()
    envelope = json.loads(output.out)
    assert envelope["status"] == "ok", output
    assert envelope["result"]["registration"]["reference"] == "workspace/canonical"
    manifest = read_workspace_manifest(workspace)
    assert manifest["brain"] == "specific-brain"
    assert manifest["slug"] == "canonical"
    assert manifest["schema_version"] == 1
    assert "workspace" not in manifest.get("links", {})


def test_folder_selection_keeps_stale_binding_and_ordinary_resolution_strict(command_vault_clone, tmp_path, monkeypatch):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    vault_registry.register(root, "default-brain")
    vault_registry.set_default("default-brain")
    with pytest.raises(WorkspaceBindingError, match="has no Brain binding"):
        resolve_selected_brain(vault=None, brain_id=None, workspace=str(workspace))
    selected = resolve_folder_brain(vault=None, brain_id=None, workspace=str(workspace))
    assert selected.vault_root == root
    assert selected.workspace == workspace
    save_workspace_manifest_data(workspace, {"schema_version": 1, "brain": "missing-brain", "slug": "repo"})
    with pytest.raises(WorkspaceBindingError, match="not in the registry"):
        resolve_folder_brain(vault=None, brain_id=None, workspace=str(workspace))
    explicit = resolve_folder_brain(vault=str(root), brain_id=None, workspace=str(workspace))
    assert explicit.vault_root == root


@pytest.mark.parametrize("fields", [{"brain": 17}, {"brain": ""}, {"links": {"workspace": "old-hub"}}])
def test_folder_selection_refuses_malformed_link_before_defaulting(command_vault_clone, tmp_path, monkeypatch, fields):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    child = workspace / "child"
    child.mkdir(parents=True)
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    vault_registry.register(root, "default-brain")
    vault_registry.set_default("default-brain")
    manifest = workspace / ".brain/local/workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump(fields))
    for target in (workspace, child):
        with pytest.raises(WorkspaceBindingError, match="no valid Brain identity"):
            resolve_folder_brain(vault=None, brain_id=None, workspace=str(target))
    assert resolve_folder_brain(vault=str(root), brain_id=None, workspace=str(workspace)).vault_root == root


@pytest.mark.parametrize("command,payload", [
    (["command", "describe", "workspace.register"], None),
    (["access", "status"], {"target_command_id": "workspace.register"}),
])
def test_pre_binding_controls_use_same_default_selection(command_vault_clone, tmp_path, monkeypatch, capsys, command, payload):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "unbound repo"
    workspace.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(repo_root / "cli/brain"))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(repo_root))
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    vault_registry.register(root, "specific-brain")
    vault_registry.set_default("specific-brain")
    argv = ["--workspace", str(workspace), *command, "--json"]
    if payload is not None:
        argv.extend(["--request-json", json.dumps(payload)])

    code = run(argv)

    output = capsys.readouterr()
    assert code == 0, output
    envelope = json.loads(output.out)
    if command[0] == "command":
        assert envelope["command_id"] == "workspace.register", envelope
    else:
        assert envelope["status"] == "ok", envelope
    assert not (workspace / ".brain").exists()


def test_cli_job_workspace_override_preserves_pinned_brain(command_vault_clone, tmp_path, monkeypatch):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    other = register_other_brain(tmp_path, "other-brain")
    vault_registry.set_default("other-brain")
    monkeypatch.setenv("BRAIN_VAULT_ROOT", str(root))
    common = SimpleNamespace(vault=None, brain_id=None, workspace=str(workspace),
                             owner_attachment=SimpleNamespace(kind="cli-job"))

    selected = _resolve(common)

    assert selected.vault_root == root
    assert selected.workspace == workspace
    assert selected.vault_root != other


def test_cli_job_creates_then_authorises_folder_registration(command_vault_clone, tmp_path, monkeypatch, capfd):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "new repo"
    workspace.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(repo_root / "cli/brain"))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(repo_root))
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    vault_registry.register(root, "specific-brain")
    script = tmp_path / "workflow.py"
    script.write_text('''import json, os, subprocess, sys
sys.path.insert(0, ''' + repr(str(repo_root / "cli")) + ''')
sys.path.insert(0, ''' + repr(str(root / ".brain-core/scripts")) + ''')
from _bootstrap.owner_attachment import OwnerAttachment
attachment = OwnerAttachment.capture()
child_code = """import os, sys
sys.path.insert(0, os.path.join(os.environ['BRAIN_CLI_DISTRIBUTION_ROOT'], 'cli'))
sys.path.insert(0, os.path.join(os.environ['BRAIN_VAULT_ROOT'], '.brain-core', 'scripts'))
from _local_cli.main import run
raise SystemExit(run(sys.argv[1:]))
"""

def call(command, request, operation=None):
    argv = [*command.split("."), "--request-json", json.dumps(request), "--json"]
    if command != "workspace.create":
        argv.extend(["--workspace", ''' + repr(str(workspace)) + '''])
    if operation is not None:
        argv.extend(["--operation", operation])
    completed = subprocess.run([sys.executable, "-c", child_code, *argv],
        capture_output=True, text=True, **attachment.forwarded_process())
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = json.loads(completed.stdout)
    return result["result"]

call("workspace.create", {"key": "canonical"})
arguments = {"slug": "canonical"}
prepared = call("access.prepare", {"preparation": {"kind": "operation",
    "command_id": "workspace.register", "arguments": arguments}})
decision = call("access.request", {"consent": {"scope": "operation",
    "operation_id": prepared["operation_id"], "digest": prepared["digest"],
    "review": prepared["review"]}})
assert decision["state"] == "authorised", decision
bound = call("workspace.register", arguments, prepared["operation_id"])
print(json.dumps(bound))
attachment.close()
''')

    code = run(["--brain", "specific-brain", "session", "run", "--", sys.executable, str(script)])

    output = capfd.readouterr()
    assert code == 0, output
    result = json.loads(output.out)
    assert result["registration"]["status"] == "attached"
    assert result["binding"]["brain"] == "specific-brain"
    manifest = read_workspace_manifest(workspace)
    assert manifest["brain"] == "specific-brain"
    assert manifest["slug"] == "canonical"
    assert manifest["schema_version"] == 1
    assert "workspace" not in manifest.get("links", {})
