"""Offline CLI identity is independent of live Brain and approval state."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cli"))

from _local_cli import main
from _launcher.version import read_cli_version_facts


@pytest.mark.parametrize("argv", [["--version"], ["version"], ["version", "--json"], ["--version", "--json"]])
def test_version_precedes_live_owners(argv, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(main.PROCESS_CONTEXT_ENV, "invalid-owner")

    def unexpected(*args, **kwargs):
        pytest.fail("offline version attempted live attachment or Brain discovery")

    monkeypatch.setattr(main.OwnerAttachment, "capture", unexpected)
    monkeypatch.setattr(main, "resolve_selected_brain", unexpected)
    monkeypatch.setattr(main, "compose_launcher_context", unexpected)
    before = sorted((str(path.relative_to(tmp_path)), path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file())
    assert main.run(argv) == 0
    captured = capsys.readouterr()
    assert not captured.err
    facts = read_cli_version_facts(ROOT)
    if "--json" in argv:
        envelope = json.loads(captured.out)
        result = envelope["result"]
        assert envelope["command"] == "brain.version"
        assert result["cli_version"] == facts.cli_version
        assert result["distributed_with_core_version"] == facts.distributed_with_core_version
        assert result["full_support_core_version"] == facts.full_support_core_version
        assert result["launcher_catalogue_fingerprint"].startswith("sha256:")
        assert "published" not in result
    else:
        assert captured.out.splitlines() == [
            f"brain {facts.cli_version}",
            f"Distributed with Brain Core: {facts.distributed_with_core_version}",
            f"Full support from Brain Core: {facts.full_support_core_version or 'unknown (pending release)'}",
        ]
    after = sorted((str(path.relative_to(tmp_path)), path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file())
    assert after == before


@pytest.mark.parametrize("damage", ["missing", "duplicate", "conflict"])
def test_missing_or_corrupt_metadata_is_not_relabelled(tmp_path, damage):
    (tmp_path / "cli").mkdir()
    (tmp_path / "src/brain-core").mkdir(parents=True)
    (tmp_path / "src/brain-core/VERSION").write_text("0.74.0\n")
    unix = 'BRAIN_CLI_VERSION="6.1.0"\nBRAIN_INSTALL_REF="v0.74.0"\n'
    windows = 'set "BRAIN_CLI_VERSION=6.1.0"\nset "BRAIN_INSTALL_REF=v0.74.0"\n'
    (tmp_path / "cli/brain").write_text(unix * (2 if damage == "duplicate" else 1))
    if damage != "missing":
        (tmp_path / "cli/brain.cmd").write_text(windows.replace("6.1.0", "6.2.0") if damage == "conflict" else windows)
    with pytest.raises(ValueError, match="metadata is missing or corrupt"):
        read_cli_version_facts(tmp_path)


def test_version_metadata_failure_is_actionable_without_traceback(monkeypatch, capsys):
    def broken():
        raise ValueError("CLI version metadata is missing or corrupt: missing Core VERSION")

    monkeypatch.setattr(main, "version_result", broken)
    assert main.run(["version", "--json"]) == 4
    result = capsys.readouterr()
    assert not result.out
    assert "missing Core VERSION" in result.err
    assert "Traceback" not in result.err
