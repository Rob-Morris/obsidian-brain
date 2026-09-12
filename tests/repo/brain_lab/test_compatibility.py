from __future__ import annotations

from pathlib import Path

import pytest

from brain_lab.compatibility import CompatibilityManifest, parse_version
from brain_lab.container_contract import CONTAINER_PYTHON


MANIFEST = Path(__file__).resolve().parents[3] / "tools" / "brain-lab" / "compatibility.json"


def test_compatibility_families_are_non_overlapping_and_cover_selected_versions():
    manifest = CompatibilityManifest(MANIFEST)

    assert manifest.select("0.51.0").adapter_id == "brain-0.51-0.54"
    assert manifest.select("0.54.9").adapter_id == "brain-0.51-0.54"
    assert manifest.select("0.55.0").adapter_id == "brain-0.55-0.63"
    assert manifest.select("0.62.0").adapter_id == "brain-0.55-0.63"
    assert manifest.select("0.63.0").adapter_id == "brain-0.55-0.63"
    with pytest.raises(ValueError, match="no unique"):
        manifest.select("0.50.0")
    with pytest.raises(ValueError, match="no unique"):
        manifest.select("0.64.0")


def test_commands_render_argv_without_shell_interpolation():
    adapter = CompatibilityManifest(MANIFEST).select("0.62.0")
    values = {
        "container_python": CONTAINER_PYTHON,
        "source": "/source",
        "vault": "/vault",
        "version": "0.62.0",
    }

    assert adapter.revision == 8
    assert adapter.render(adapter.install, values) == (
        "bash",
        "/source/install.sh",
        "--non-interactive",
        "/vault",
    )
    rehydrate = [adapter.render(command, values) for command in adapter.rehydrate]
    assert rehydrate[0][1] == "/usr/local/lib/brain-lab/clear_imported_state.py"
    assert rehydrate[1][3] == '{"vault_root":"/vault"}'
    assert "codex" in rehydrate[2]
    assert rehydrate[3][-1] == ".gitignore"
    cli_version = next(gate for gate in adapter.health if gate.gate_id == "cli-version")
    assert cli_version.expected_stdout == "brain {cli_version}"
    assert cli_version.required_for == ("template", "rehydrate")
    session = next(gate for gate in adapter.health if gate.gate_id == "session")
    assert session.retry is not None
    assert session.retry.retryable_error_codes == ("conflict",)
    assert session.retry.maximum_attempts == 40
    doctor = next(gate for gate in adapter.health if gate.gate_id == "doctor")
    assert doctor.expected_json is not None
    assert doctor.expected_json["result.cli.version"] == "{cli_version}"
    assert doctor.required_for == ("template",)
    machine_doctor = next(gate for gate in adapter.health if gate.gate_id == "machine-doctor")
    assert machine_doctor.required_for == ("rehydrate",)
    assert machine_doctor.expected_json == {
        "healthy": True,
        "tidy": True,
        "launcher_python": "{container_python}",
        "counts.repair_findings": 0,
    }
    mcp = next(gate for gate in adapter.health if gate.gate_id == "mcp-read-only")
    assert mcp.command[-2:] == ("--contract", "canonical")
    assert mcp.expected_json == {"read_only_round_trip": "tools/call:command.list"}
    paths = next(gate for gate in adapter.health if gate.gate_id == "active-paths")
    assert paths.expected_json == {"safe": True}


def test_historical_adapter_owns_future_dependency_break_and_generated_template_state():
    adapter = CompatibilityManifest(MANIFEST).select("0.51.0")
    values = {
        "container_python": CONTAINER_PYTHON,
        "source": "/source",
        "vault": "/vault",
        "version": "0.51.0",
    }

    post_install = [adapter.render(command, values) for command in adapter.post_install]
    template_prepare = [adapter.render(command, values) for command in adapter.template_prepare]
    session = next(gate for gate in adapter.health if gate.gate_id == "session")

    assert any("mcp==1.29.0" in command for command in post_install)
    assert any(
        any(part.endswith("/repair.py") for part in command) and "router" in command
        for command in template_prepare
    )
    assert any(
        any(part.endswith("/repair.py") for part in command) and "lexical" in command
        for command in template_prepare
    )
    rehydrate = [adapter.render(command, values) for command in adapter.rehydrate]
    restore = rehydrate[-1]
    assert adapter.revision == 12
    assert rehydrate[0][1] == "/usr/local/lib/brain-lab/clear_imported_state.py"
    assert "codex" in rehydrate[2]
    assert any("mcp" in command and any(part.endswith("/repair.py") for part in command) for command in rehydrate)
    assert restore[1] == "/usr/local/lib/brain-lab/restore_seed_paths.py"
    assert restore.count("--path") == 4
    assert "CLAUDE.md" not in restore
    machine_doctor = next(gate for gate in adapter.health if gate.gate_id == "machine-doctor")
    assert machine_doctor.required_for == ("rehydrate",)
    assert session.command == ("brain", "session", "--json")
    assert session.retry is None
    mcp = next(gate for gate in adapter.health if gate.gate_id == "mcp-read-only")
    assert mcp.command[-2:] == ("--contract", "legacy")
    assert mcp.expected_json == {"read_only_round_trip": "tools/call:brain_init"}


@pytest.mark.parametrize("value", ["1", "1.2", "v1.2.x", "1.2.3.4"])
def test_version_parser_rejects_ambiguous_versions(value: str):
    with pytest.raises(ValueError):
        parse_version(value)
