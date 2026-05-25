import json
import os
from pathlib import Path
import sys

import doctor_machine
from _common import central_venvs_root, resolve_vault_venv_python
from _machine.discovery import discover_brains, machine_registry_path, sync_machine_registry
from _machine.maintenance import inspect_machine_runtime_state
import vault_registry


def _make_vault(root: Path, name: str) -> Path:
    vault = root / name
    (vault / ".brain-core" / "brain_mcp").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.99.0\n")
    (vault / ".brain-core" / "brain_mcp" / "requirements.txt").write_text("mcp==1.0.0\n")
    return vault


def _install_central_runtime(python_path: Path) -> None:
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.symlink_to(sys.executable)


def test_discover_brains_skips_registry_writes_until_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    current = _make_vault(tmp_path, "Current Brain")
    registered = _make_vault(tmp_path, "Registered Brain")
    stale = tmp_path / "Missing Brain"

    vault_registry.register(str(registered))
    registry_path = Path(os.environ["HOME"]) / ".config" / "brain" / "vaults"
    registry_path.write_text(
        registry_path.read_text() + f"missing\t{stale}\n",
    )

    summary = discover_brains(current_vault=current)

    assert [brain["alias"] for brain in summary["brains"]] == [None, "registered-brain"]
    assert summary["brains"][0]["sources"] == ["current"]
    assert summary["brains"][1]["sources"] == ["vault_registry"]
    assert summary["stale_registry_entries"] == [
        {"alias": "missing", "path": str(stale.resolve())},
    ]
    assert summary["machine_registry_view"]["path"] == str(machine_registry_path())
    assert not machine_registry_path().exists()

    sync = sync_machine_registry(summary["brains"])
    assert sync["changed"]
    assert machine_registry_path().exists()

    registry_state = json.loads(machine_registry_path().read_text())
    assert registry_state["version"] == 1
    assert [brain["alias"] for brain in registry_state["brains"]] == [None, "registered-brain"]
    assert [brain["path"] for brain in registry_state["brains"]] == [
        str(current.resolve()),
        str(registered.resolve()),
    ]


def test_discover_brains_uses_machine_registry_as_a_root(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    registered = _make_vault(tmp_path, "Registered Brain")

    first = discover_brains(current_vault=registered)
    sync = sync_machine_registry(first["brains"])
    assert sync["changed"]

    second = discover_brains()

    assert [brain["path"] for brain in second["brains"]] == [str(registered.resolve())]
    assert second["brains"][0]["sources"] == ["machine_registry"]
    assert second["machine_registry_view"]["version"] == 1


def test_discover_brains_reports_stale_machine_registry_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    active = _make_vault(tmp_path, "Active Brain")
    missing = tmp_path / "Missing Brain"
    machine_registry_path().parent.mkdir(parents=True, exist_ok=True)
    machine_registry_path().write_text(
        json.dumps(
            {
                "version": 1,
                "brains": [
                    {"alias": "active-brain", "path": str(active)},
                    {"alias": "missing-brain", "path": str(missing)},
                ],
            },
            indent=2,
        )
    )

    summary = discover_brains()

    assert summary["stale_machine_registry_entries"] == [
        {"alias": "missing-brain", "path": str(missing.resolve())}
    ]

    sync = sync_machine_registry(summary["brains"])
    assert sync["changed"]
    assert sync["stale_machine_registry_entries"] == [
        {"alias": "missing-brain", "path": str(missing.resolve())}
    ]
    registry_state = json.loads(machine_registry_path().read_text())
    assert registry_state["brains"] == [
        {"alias": "active-brain", "path": str(active.resolve())}
    ]


def test_sync_machine_registry_rewrites_malformed_v1_state_with_backup(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    active = _make_vault(tmp_path, "Active Brain")
    machine_registry_path().parent.mkdir(parents=True, exist_ok=True)
    machine_registry_path().write_text(
        json.dumps(
            {
                "version": 1,
                "brains": [
                    {"alias": 7, "path": str(active)},
                    ["not-a-dict"],
                ],
            },
            indent=2,
        )
    )

    summary = discover_brains(current_vault=active)
    sync = sync_machine_registry(summary["brains"])

    assert sync["changed"]
    assert sync["malformed_rewritten"]
    assert sync["backup_path"] is not None
    assert Path(sync["backup_path"]).is_file()
    registry_state = json.loads(machine_registry_path().read_text())
    assert registry_state["version"] == 1
    assert registry_state["brains"] == [
        {"alias": None, "path": str(active.resolve())}
    ]


def test_sync_machine_registry_blocks_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    active = _make_vault(tmp_path, "Active Brain")
    machine_registry_path().parent.mkdir(parents=True, exist_ok=True)
    machine_registry_path().write_text("{not json\n")
    original = machine_registry_path().read_text()

    summary = discover_brains(current_vault=active)
    sync = sync_machine_registry(summary["brains"])

    assert summary["machine_registry_view"]["blocked"]
    assert summary["machine_registry_view"]["blocked_reason"] == "invalid-json"
    assert sync["blocked"]
    assert sync["blocked_reason"] == "invalid-json"
    assert not sync["changed"]
    assert machine_registry_path().read_text() == original


def test_sync_machine_registry_refuses_newer_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    active = _make_vault(tmp_path, "Active Brain")
    machine_registry_path().parent.mkdir(parents=True, exist_ok=True)
    machine_registry_path().write_text(
        json.dumps(
            {
                "version": 2,
                "brains": [{"alias": "active-brain", "path": str(active)}],
            },
            indent=2,
        )
    )
    original = machine_registry_path().read_text()

    summary = discover_brains(current_vault=active)
    sync = sync_machine_registry(summary["brains"])

    assert summary["machine_registry_view"]["blocked"]
    assert summary["machine_registry_view"]["blocked_reason"] == "newer-version"
    assert sync["blocked"]
    assert not sync["changed"]
    assert machine_registry_path().read_text() == original


def test_inspect_machine_runtime_state_classifies_selected_and_orphan_runtimes(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    _install_central_runtime(selected_runtime)

    orphan_runtime = central_venvs_root() / "py3.12-orphan0000000000" / "bin" / "python"
    _install_central_runtime(orphan_runtime)

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    assert summary["counts"]["brains"] == 1
    assert summary["counts"]["machine_registry_brains"] == 1
    assert summary["brains"][0]["runtime"]["status"] == "central_exact"
    assert summary["brains"][0]["runtime"]["selected_runtime"] == str(selected_runtime)
    assert summary["counts"]["orphan_candidates"] == 1
    assert any(
        runtime["python"] == str(orphan_runtime) and runtime["orphan_candidate"]
        for runtime in summary["runtimes"]
    )
    assert summary["healthy"]
    assert not summary["tidy"]


def test_inspect_machine_runtime_state_marks_orphans_unknown_when_ps_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    _install_central_runtime(selected_runtime)

    orphan_runtime = central_venvs_root() / "py3.12-orphan0000000000" / "bin" / "python"
    _install_central_runtime(orphan_runtime)

    def _boom(*args, **kwargs):
        raise OSError("ps unavailable")

    monkeypatch.setattr("_machine.topology.subprocess.run", _boom)

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    assert summary["live_process_scan_available"] is False
    assert summary["counts"]["orphan_candidates"] == 0
    assert all(runtime["orphan_candidate"] is None for runtime in summary["runtimes"])
    assert summary["healthy"]
    assert not summary["tidy"]


def test_doctor_machine_main_renders_human_and_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    _install_central_runtime(selected_runtime)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "doctor_machine.py",
            "--vault",
            str(vault),
            "--current-vault",
            str(vault),
            "--launcher",
            sys.executable,
        ],
    )
    assert doctor_machine.main() == 0
    human = capsys.readouterr().out
    assert "brains:" in human
    assert "registry:" in human
    assert "brain routes:" in human

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "doctor_machine.py",
            "--vault",
            str(vault),
            "--current-vault",
            str(vault),
            "--launcher",
            sys.executable,
            "--json",
        ],
    )
    assert doctor_machine.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["brains"] == 1
    assert payload["machine_registry"]["brains"] == 1
    assert payload["brains"][0]["runtime"]["status"] == "central_exact"
