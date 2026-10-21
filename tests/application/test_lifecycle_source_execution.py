"""Selected lifecycle policy and its imports come from one source snapshot."""

import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from _bootstrap import machine_cli

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "cli") not in sys.path:
    sys.path.insert(0, str(REPO / "cli"))
from _local_cli import main


@pytest.fixture
def selected_source(tmp_path):
    source = tmp_path / "selected"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    shutil.copytree(REPO / "cli", source / "cli", ignore=ignore)
    shutil.copytree(REPO / "src/brain-core/scripts", source / "src/brain-core/scripts", ignore=ignore)
    core = source / "src/brain-core"
    shutil.copy2(REPO / "src/brain-core/VERSION", core / "VERSION")
    shutil.copy2(REPO / "src/brain-core/command-catalogue.json", core / "command-catalogue.json")
    (core / "brain_mcp").mkdir()
    shutil.copy2(REPO / "src/brain-core/brain_mcp/_interface_protocol.py", core / "brain_mcp/_interface_protocol.py")
    (source / "template-vault").mkdir()
    (core / "scripts/release_helper.py").write_text("MESSAGE = 'policy from selected B'\n")
    return source


def test_selecting_b_does_not_reuse_a_install_policy_or_cached_helpers(
    selected_source, tmp_path, monkeypatch, capsys,
):
    import install
    monkeypatch.setattr(install, "_destination_error", lambda *_: "policy from running A")
    monkeypatch.setitem(sys.modules, "release_helper", SimpleNamespace(MESSAGE="cached A"))
    (selected_source / "src/brain-core/scripts/install.py").write_text(
        "from release_helper import MESSAGE\ndef _destination_error(*args):\n    return MESSAGE\n"
    )
    binary = tmp_path / "brain"
    binary.write_text('BRAIN_CLI_VERSION="8.0.0"\n')
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(binary))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(REPO))
    monkeypatch.delenv(machine_cli.LIFECYCLE_SOURCE_ENV, raising=False)
    code = main.run(["install", "--dry-run", "--json", "--request-json", json.dumps({
        "source_root": str(selected_source), "vault_root": str(tmp_path / "target"), "brain_id": "test", "mcp_scope": "skip",
    })])
    captured = capsys.readouterr()
    assert code == 2, captured
    assert json.loads(captured.out)["error"]["message"] == "policy from selected B"
    assert not (tmp_path / "target").exists()
    assert binary.read_text() == 'BRAIN_CLI_VERSION="8.0.0"\n'


@pytest.mark.parametrize(("identity", "replace_cli"), [("uninstalled", False), ("unknown", True), ("8.0.0", False)])
def test_source_upgrade_uses_selected_helper_and_honest_cli_identity(
    selected_source, tmp_path, monkeypatch, identity, replace_cli,
):
    version = (selected_source / "src/brain-core/VERSION").read_text().strip()
    (selected_source / "src/brain-core/scripts/upgrade.py").write_text(
        "from pathlib import Path\nfrom release_helper import MESSAGE\n"
        "def upgrade(vault, core, **kwargs):\n"
        "    return {'status':'ok', 'old_version':'0.55.0', "
        "'new_version':Path(core, 'VERSION').read_text().strip(), 'files_modified':[MESSAGE]}\n"
    )
    binary = tmp_path / "brain"
    if identity != "uninstalled":
        binary.write_text('BRAIN_CLI_VERSION="8.0.0"\n' if identity != "unknown" else "working wrapper without declaration\n")
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.55.0\n")
    registry = tmp_path / "config/brain/vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"# brain registry v2\ntest\tlocal\t{vault}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    request = {"source_root": str(selected_source), "replace_cli": replace_cli}
    completed = machine_cli.run_source_cli(selected_source, binary, [
        "upgrade", "--vault", str(vault), "--dry-run", "--json", "--request-json", json.dumps(request),
    ], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = json.loads(completed.stdout)["result"]
    assert result["files_modified"] == 1
    assert result["new_version"] == version
    assert result["preflight"]["old_cli_version"] == identity
    assert result["preflight"]["new_cli_version"] == ("6.2.0" if replace_cli else identity)
    assert binary.exists() is (identity != "uninstalled")
    assert (vault / ".brain-core/VERSION").read_text() == "0.55.0\n"


def test_source_child_refuses_a_lost_pin_without_acquiring(selected_source, tmp_path):
    binary = tmp_path / "absent-brain"
    completed = machine_cli.run_source_cli(selected_source, binary, [
        "upgrade", "--json", "--dry-run", "--request-json", '{"version":"latest"}',
    ], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 2
    assert "lost its source pin" in completed.stderr


@pytest.mark.parametrize('stdout', ['', '{}', 'not-json'])
def test_selected_source_cannot_succeed_without_a_valid_json_receipt(tmp_path, monkeypatch, stdout):
    common = SimpleNamespace(vault=None, brain_id=None, workspace=None, operator_key=None,
                             dry_run=False, json_mode=True)
    monkeypatch.setattr(machine_cli, 'run_source_cli', lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout=stdout, stderr=''))
    with pytest.raises(main.CliError, match='invalid receipt'):
        main._run_selected_lifecycle(['install'], common, {'source_root': str(tmp_path / 'source')},
                                     tmp_path / 'brain', REPO, None)
