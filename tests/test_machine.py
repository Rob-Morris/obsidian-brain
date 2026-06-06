import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import doctor_machine
import machine
from _common import _venv, central_venvs_root, resolve_vault_venv_python
from _machine.discovery import discover_brains, machine_registry_path, sync_machine_registry
from _machine.maintenance import inspect_machine_runtime_state, migrate_legacy_brains, prune_orphaned_runtimes
from _machine.topology import classify_brain_runtime, find_live_brain_runtime_processes, list_central_runtimes
import vault_registry


@pytest.fixture(autouse=True)
def _fast_machine_process_scan(monkeypatch):
    """Machine-summary tests default to no live runtimes without shelling out to ps."""
    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        lambda runtime_pythons: {
            "available": True,
            "processes": {str(Path(p)): [] for p in runtime_pythons},
        },
    )


def _make_vault(root: Path, name: str) -> Path:
    vault = root / name
    (vault / ".brain-core" / "brain_mcp").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.99.0\n")
    (vault / ".brain-core" / "brain_mcp" / "requirements.txt").write_text("mcp==1.0.0\n")
    return vault


def _install_central_runtime(python_path: Path) -> None:
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.symlink_to(sys.executable)


def _make_drifted_vault(root: Path, name: str) -> Path:
    vault = _make_vault(root, name)
    (vault / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "brain": {
                        "command": "/tmp/old-python",
                        "args": ["-m", "brain_mcp.proxy", "/tmp/old-python", "brain_mcp.server"],
                        "env": {"BRAIN_VAULT_ROOT": str(vault), "PYTHONPATH": str(vault / ".brain-core")},
                    }
                }
            },
            indent=2,
        )
    )
    (vault / ".brain" / "local").mkdir(parents=True, exist_ok=True)
    (vault / ".brain" / "local" / "workspaces.json").write_text(
        json.dumps({"workspaces": {"bad": 7}}, indent=2)
    )
    return vault


def test_discover_brains_skips_registry_writes_until_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    current = _make_vault(tmp_path, "Current Brain")
    registered = _make_vault(tmp_path, "Registered Brain")
    stale = tmp_path / "Missing Brain"

    vault_registry.register(str(registered))
    registry_path = Path(os.environ["HOME"]) / ".config" / "brain" / "vaults"
    registry_path.write_text(
        registry_path.read_text() + f"missing\tlocal\t{stale}\n",
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


def test_discover_brains_ignores_non_local_authoritative_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    current = _make_vault(tmp_path, "Current Brain")
    registry_path = Path(os.environ["HOME"]) / ".config" / "brain" / "vaults"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("team\tremote\thttps://brain.example.com\n")

    summary = discover_brains(current_vault=current)

    assert [brain["alias"] for brain in summary["brains"]] == [None]
    assert summary["stale_registry_entries"] == []


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


def test_classify_brain_runtime_preserves_venv_symlink_boundary(monkeypatch, tmp_path):
    expected = tmp_path / "expected" / "bin" / "python"
    selected = tmp_path / "selected" / "bin" / "python"
    expected.parent.mkdir(parents=True)
    selected.parent.mkdir(parents=True)
    expected.symlink_to(sys.executable)
    selected.symlink_to(sys.executable)
    vault = _make_vault(tmp_path, "Active Brain")

    monkeypatch.setattr("_machine.topology.resolve_vault_venv_python", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr("_machine.topology.find_existing_central_venv", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr("_machine.topology.find_runnable_python", lambda *_args, **_kwargs: selected)

    runtime = classify_brain_runtime(vault, launcher_python=sys.executable)

    assert runtime["status"] == "central_compatible"
    assert runtime["expected_runtime"] == str(expected)
    assert runtime["selected_runtime"] == str(selected)


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
    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        find_live_brain_runtime_processes,
    )

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    assert summary["live_process_scan_available"] is False
    assert summary["counts"]["orphan_candidates"] == 0
    assert all(runtime["orphan_candidate"] is False for runtime in summary["runtimes"])
    assert summary["healthy"]
    assert not summary["tidy"]


def test_find_live_brain_runtime_processes_matches_spaced_python_family_symlinks(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime with spaces"
    runtime_python = runtime_root / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.symlink_to(sys.executable)
    (runtime_python.parent / "python3.12").symlink_to("python")
    alias_root = tmp_path / "runtime alias"
    alias_root.symlink_to(runtime_root, target_is_directory=True)

    def _fake_run(*args, **kwargs):
        command = f"{alias_root / 'bin' / 'python3.12'} -m brain_mcp.server"
        return subprocess.CompletedProcess(args[0], 0, f"123 {command}\n", "")

    monkeypatch.setattr("_machine.topology.subprocess.run", _fake_run)

    live = find_live_brain_runtime_processes([runtime_python])

    assert live["available"] is True
    assert live["processes"][str(runtime_python)] == [
        {"pid": 123, "command": f"{alias_root / 'bin' / 'python3.12'} -m brain_mcp.server"}
    ]


def test_find_live_brain_runtime_processes_matches_python_family_names(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_python = runtime_root / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.symlink_to(sys.executable)
    pythonw = runtime_python.parent / "pythonw"
    pythonw.symlink_to("python")

    def _fake_run(*args, **kwargs):
        command = f"{pythonw} -m brain_mcp.server"
        return subprocess.CompletedProcess(args[0], 0, f"123 {command}\n", "")

    monkeypatch.setattr("_machine.topology.subprocess.run", _fake_run)

    live = find_live_brain_runtime_processes([runtime_python])

    assert live["available"] is True
    assert live["processes"][str(runtime_python)] == [
        {"pid": 123, "command": f"{pythonw} -m brain_mcp.server"}
    ]


def test_find_live_brain_runtime_processes_caches_parent_realpath(monkeypatch, tmp_path):
    runtime_python = tmp_path / "runtime" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.touch()
    original_realpath = os.path.realpath
    realpath_calls: list[str] = []

    def _fake_realpath(path):
        realpath_calls.append(path)
        return original_realpath(path)

    def _fake_run(*args, **kwargs):
        command = f"{runtime_python} -m brain_mcp.server"
        stdout = "\n".join(
            [
                "100 /usr/bin/ssh some-host",
                f"101 {command}",
                "102 /bin/echo python is only an argument",
                f"103 {command}",
            ]
        )
        return subprocess.CompletedProcess(args[0], 0, f"{stdout}\n", "")

    monkeypatch.setattr("_machine.topology.os.path.realpath", _fake_realpath)
    monkeypatch.setattr("_machine.topology.subprocess.run", _fake_run)

    live = find_live_brain_runtime_processes([runtime_python])

    assert live["available"] is True
    assert live["processes"][str(runtime_python)] == [
        {"pid": 101, "command": f"{runtime_python} -m brain_mcp.server"},
        {"pid": 103, "command": f"{runtime_python} -m brain_mcp.server"},
    ]
    # One tracked-side normalisation plus one cached scan-side parent lookup.
    assert realpath_calls == [str(runtime_python.parent), str(runtime_python.parent)]


def test_list_central_runtimes_uses_platform_venv_python(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_venv.sys, "platform", "win32")

    python_path = tmp_path / ".brain" / "venvs" / "py3.12-deadbeef" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.touch()

    runtimes = list_central_runtimes()

    assert runtimes == [
        {
            "name": "py3.12-deadbeef",
            "dir": str(python_path.parent.parent),
            "python": str(python_path),
        }
    ]



def test_classify_brain_runtime_reports_launcher_fallback(monkeypatch, tmp_path):
    vault = _make_vault(tmp_path, "Fallback Brain")

    monkeypatch.setattr("_machine.topology.find_existing_central_venv", lambda vault_path, launcher=None: None)
    monkeypatch.setattr("_machine.topology.find_runnable_python", lambda vault_path, launcher=None: Path(sys.executable))

    runtime = classify_brain_runtime(vault, launcher_python=sys.executable)

    assert runtime["status"] == "launcher_fallback"
    assert "falling back to the bare launcher" in runtime["message"]



def test_classify_brain_runtime_reports_missing_runtime(monkeypatch, tmp_path):
    vault = _make_vault(tmp_path, "Missing Brain")

    monkeypatch.setattr("_machine.topology.find_existing_central_venv", lambda vault_path, launcher=None: None)
    monkeypatch.setattr("_machine.topology.find_runnable_python", lambda vault_path, launcher=None: None)

    runtime = classify_brain_runtime(vault, launcher_python=sys.executable)

    assert runtime["status"] == "missing_runtime"
    assert "has no central runtime" in runtime["message"]



def test_inspect_machine_runtime_state_reports_brain_level_repair_findings(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Active Brain")
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    brain = summary["brains"][0]
    scopes = {finding["repair"]["scope"] for finding in brain["repair_findings"]}

    assert scopes == {"mcp", "registry"}
    assert summary["counts"]["brains_with_repair_findings"] == 1
    assert summary["counts"]["repair_findings"] == 3
    assert not summary["healthy"]


def test_migrate_legacy_brains_reports_missing_selector(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    result = migrate_legacy_brains(
        summary,
        launcher_python=sys.executable,
        dry_run=False,
        selector="missing-brain",
    )

    assert result["status"] == "error"
    assert result["counts"]["targets"] == 0
    assert result["steps"][0]["name"] == "selection"
    assert "No discovered Brain matches" in result["steps"][0]["message"]



def test_migrate_legacy_brains_reports_non_legacy_selector_as_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    _install_central_runtime(selected_runtime)
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    result = migrate_legacy_brains(
        summary,
        launcher_python=sys.executable,
        dry_run=False,
        selector=str(vault.resolve()),
    )

    assert result["status"] == "noop"
    assert result["counts"]["targets"] == 0
    assert result["steps"][0]["name"] == "selection"
    assert "is not currently using a legacy vault-local .venv" in result["steps"][0]["message"]



def test_migrate_legacy_brains_dry_run_plans_runtime_and_venv_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    def _fake_run(argv, capture_output, text, timeout, check):
        payload = {"scope": argv[2], "status": "planned", "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_run)

    result = migrate_legacy_brains(
        summary,
        launcher_python=sys.executable,
        dry_run=True,
    )

    assert result["status"] == "planned"
    assert result["counts"]["targets"] == 1
    target = result["targets"][0]
    assert [step["status"] for step in target["steps"]] == ["planned", "planned", "planned", "planned"]
    assert "Would remove the legacy vault-local .venv" in target["steps"][3]["message"]
    assert legacy_python.parent.parent.exists()



def test_migrate_legacy_brains_delegates_repairs_and_removes_legacy_venv(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))

    def _fake_run(argv, capture_output, text, timeout, check):
        scope = argv[2]
        if scope == "runtime":
            _install_central_runtime(selected_runtime)
        payload = {"scope": scope, "status": "ok", "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_run)

    result = migrate_legacy_brains(
        summary,
        launcher_python=sys.executable,
        dry_run=False,
    )

    assert result["status"] == "ok"
    assert result["counts"]["targets"] == 1
    assert not legacy_python.parent.parent.exists()
    assert Path(selected_runtime).is_file()
    target = result["targets"][0]
    assert [step["name"] for step in target["steps"]] == ["runtime", "mcp", "registry", "legacy_venv", "verify"]
    assert "repair.py" in target["steps"][0]["command"] and " runtime " in target["steps"][0]["command"] and "--json" in target["steps"][0]["command"]
    assert "repair.py" in target["steps"][1]["command"] and " mcp " in target["steps"][1]["command"] and "--json" in target["steps"][1]["command"]
    assert "repair.py" in target["steps"][2]["command"] and " registry " in target["steps"][2]["command"] and "--json" in target["steps"][2]["command"]


def test_prune_orphaned_runtimes_removes_orphans(monkeypatch, tmp_path):
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

    result = prune_orphaned_runtimes(summary, dry_run=False)

    assert result["status"] == "ok"
    assert result["counts"]["targets"] == 1
    assert not orphan_runtime.parent.parent.exists()
    assert Path(selected_runtime).is_file()


def test_migrate_legacy_brains_keeps_legacy_venv_on_partial_delegated_repair(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    def _fake_run(argv, capture_output, text, timeout, check):
        scope = argv[2]
        status = "partial" if scope == "mcp" else "ok"
        payload = {"scope": scope, "status": status, "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_run)

    result = migrate_legacy_brains(
        summary,
        launcher_python=sys.executable,
        dry_run=False,
    )

    assert result["status"] == "partial"
    target = result["targets"][0]
    assert target["steps"][1]["status"] == "partial"
    assert target["steps"][3]["status"] == "noop"
    assert legacy_python.parent.parent.exists()


def test_migrate_legacy_brains_keeps_legacy_venv_when_live_process_detected(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))

    def _fake_repair_run(argv, capture_output, text, timeout, check):
        if argv[2] == "runtime":
            _install_central_runtime(selected_runtime)
        payload = {"scope": argv[2], "status": "ok", "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_repair_run)
    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        lambda runtime_pythons: {
            "available": True,
            "processes": {str(legacy_python): [{"pid": 123, "command": f"{legacy_python} -m brain_mcp.server"}]},
        },
    )

    result = migrate_legacy_brains(summary, launcher_python=sys.executable, dry_run=False)

    assert result["status"] == "partial"
    target = result["targets"][0]
    assert target["steps"][3]["status"] == "error"
    assert "still in use by a live process" in target["steps"][3]["message"]
    assert legacy_python.parent.parent.exists()



def test_migrate_legacy_brains_keeps_legacy_venv_when_live_scan_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))

    def _fake_repair_run(argv, capture_output, text, timeout, check):
        if argv[2] == "runtime":
            _install_central_runtime(selected_runtime)
        payload = {"scope": argv[2], "status": "ok", "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_repair_run)
    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        lambda runtime_pythons: {"available": False, "processes": {str(legacy_python): []}},
    )

    result = migrate_legacy_brains(summary, launcher_python=sys.executable, dry_run=False)

    assert result["status"] == "partial"
    target = result["targets"][0]
    assert target["steps"][3]["status"] == "error"
    assert "live-process detection is unavailable" in target["steps"][3]["message"]
    assert legacy_python.parent.parent.exists()



@pytest.mark.parametrize(
    ("run_factory", "expected_fragment"),
    [
        (
            lambda: (lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom"))),
            "Could not run target Brain repair scope runtime",
        ),
        (
            lambda: (lambda argv, capture_output, text, timeout, check: subprocess.CompletedProcess(argv, 0, "not-json", "")),
            "did not produce valid JSON",
        ),
        (
            lambda: (lambda argv, capture_output, text, timeout, check: subprocess.CompletedProcess(argv, 0, json.dumps({"scope": argv[2], "status": "mystery"}), "")),
            "returned unknown status 'mystery'",
        ),
    ],
)
def test_migrate_legacy_brains_keeps_legacy_venv_on_repair_scope_errors(
    monkeypatch,
    tmp_path,
    run_factory,
    expected_fragment,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)
    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    monkeypatch.setattr("_machine.maintenance.subprocess.run", run_factory())
    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        lambda runtime_pythons: {"available": True, "processes": {str(legacy_python): []}},
    )

    result = migrate_legacy_brains(summary, launcher_python=sys.executable, dry_run=False)

    assert result["status"] == "partial"
    target = result["targets"][0]
    assert target["steps"][0]["status"] == "error"
    assert expected_fragment in target["steps"][0]["message"]
    assert target["steps"][3]["status"] == "noop"
    assert legacy_python.parent.parent.exists()



def test_migrate_legacy_brains_dry_run_reports_live_scan_uncertainty(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    def _fake_run(argv, capture_output, text, timeout, check):
        payload = {"scope": argv[2], "status": "planned", "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        lambda runtime_pythons: {"available": False, "processes": {str(legacy_python): []}},
    )

    result = migrate_legacy_brains(summary, launcher_python=sys.executable, dry_run=True)

    assert result["status"] == "planned"
    target = result["targets"][0]
    assert target["steps"][3]["status"] == "planned"
    assert "proving no live process still uses it" in target["steps"][3]["message"]



def test_prune_orphaned_runtimes_keeps_live_unclaimed_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    _install_central_runtime(selected_runtime)
    orphan_runtime = central_venvs_root() / "py3.12-orphan0000000000" / "bin" / "python"
    _install_central_runtime(orphan_runtime)

    monkeypatch.setattr(
        "_machine.maintenance.find_live_brain_runtime_processes",
        lambda runtime_pythons: {
            "available": True,
            "processes": {
                str(selected_runtime): [],
                str(orphan_runtime): [{"pid": 456, "command": f"{orphan_runtime} -m brain_mcp.server"}],
            },
        },
    )

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    assert summary["counts"]["orphan_candidates"] == 0
    row = next(runtime for runtime in summary["runtimes"] if runtime["python"] == str(orphan_runtime))
    assert row["orphan_candidate"] is False
    assert row["live_processes"] == [{"pid": 456, "command": f"{orphan_runtime} -m brain_mcp.server"}]

    result = prune_orphaned_runtimes(summary, dry_run=False)

    assert result["status"] == "noop"
    assert result["counts"]["targets"] == 0
    assert "No orphaned shared runtimes need pruning." in result["steps"][0]["message"]
    assert orphan_runtime.parent.parent.exists()



def test_prune_orphaned_runtimes_reports_rmtree_errors_per_target(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    orphan_one = central_venvs_root() / "py3.12-orphan0000000000" / "bin" / "python"
    orphan_two = central_venvs_root() / "py3.12-orphan1111111111" / "bin" / "python"
    _install_central_runtime(orphan_one)
    _install_central_runtime(orphan_two)

    discovery = discover_brains(current_vault=vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=sys.executable,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    calls = {"count": 0}
    real_rmtree = __import__("shutil").rmtree

    def _fake_rmtree(path):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("busy")
        return real_rmtree(path)

    monkeypatch.setattr("_machine.maintenance.shutil.rmtree", _fake_rmtree)

    result = prune_orphaned_runtimes(summary, dry_run=False)

    assert result["status"] == "partial"
    assert result["counts"]["targets"] == 2
    assert result["targets"][0]["steps"][0]["status"] == "error"
    assert result["targets"][1]["steps"][0]["status"] == "changed"
    assert orphan_one.parent.parent.exists()
    assert not orphan_two.parent.parent.exists()



def test_machine_main_renders_migrate_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Legacy Brain")
    legacy_python = vault / ".venv" / "bin" / "python"
    _install_central_runtime(legacy_python)

    def _fake_run(argv, capture_output, text, timeout, check):
        payload = {"scope": argv[2], "status": "planned", "steps": []}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr("_machine.maintenance.subprocess.run", _fake_run)

    assert machine.main(
        [
            "--vault",
            str(vault),
            "--current-vault",
            str(vault),
            "--launcher",
            sys.executable,
            "migrate-legacy",
            "--dry-run",
        ]
    ) == 0
    human = capsys.readouterr().out
    assert f"Machine action: migrate-legacy" in human
    assert f"  PLAN     {vault.resolve()}" in human

    assert machine.main(
        [
            "--vault",
            str(vault),
            "--current-vault",
            str(vault),
            "--launcher",
            sys.executable,
            "migrate-legacy",
            "--dry-run",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "migrate-legacy"
    assert payload["status"] == "planned"
    assert payload["counts"]["targets"] == 1



def test_machine_main_renders_prune_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    selected_runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    _install_central_runtime(selected_runtime)
    orphan_runtime = central_venvs_root() / "py3.12-orphan0000000000" / "bin" / "python"
    _install_central_runtime(orphan_runtime)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "machine.py",
            "--vault",
            str(vault),
            "--current-vault",
            str(vault),
            "--launcher",
            sys.executable,
            "prune-runtimes",
            "--dry-run",
        ],
    )

    assert machine.main() == 0
    human = capsys.readouterr().out
    assert f"  PLAN     {orphan_runtime}" in human

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "machine.py",
            "--vault",
            str(vault),
            "--current-vault",
            str(vault),
            "--launcher",
            sys.executable,
            "prune-runtimes",
            "--dry-run",
            "--json",
        ],
    )

    assert machine.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "prune-runtimes"
    assert payload["status"] == "planned"
    assert payload["counts"]["targets"] == 1



def test_doctor_machine_main_renders_brain_level_repair_guidance(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_drifted_vault(tmp_path, "Active Brain")
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
    assert doctor_machine.main() == 1
    human = capsys.readouterr().out
    assert "repair: mcp — Claude Brain MCP config does not point at the canonical managed Python." in human
    assert "repair: mcp — Claude SessionStart hook for brain_session is missing or does not match the canonical command." in human
    assert ".brain-core/scripts/repair.py" in human
    assert "mcp --vault" in human
    assert "repair: registry — Registry contains invalid linked-workspace entries: bad" in human
    assert "registry --vault" in human

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
    assert doctor_machine.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["brains_with_repair_findings"] == 1
    assert payload["counts"]["repair_findings"] == 3
    assert {finding["repair"]["scope"] for finding in payload["brains"][0]["repair_findings"]} == {"mcp", "registry"}


def test_doctor_machine_main_renders_default_blocked_registry_note(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    vault = _make_vault(tmp_path, "Active Brain")
    machine_registry_path().parent.mkdir(parents=True, exist_ok=True)
    machine_registry_path().write_text("{not json\n")

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
    assert doctor_machine.main() == 1
    human = capsys.readouterr().out
    assert "registry note:" in human
    assert "machine-registry state could not be safely interpreted; leaving brains.json untouched" in human



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
    assert payload["counts"]["brains_with_repair_findings"] == 0
    assert payload["counts"]["repair_findings"] == 0
    assert payload["machine_registry"]["brains_count"] == 1
    assert payload["brains"][0]["runtime"]["status"] == "central_exact"
    assert payload["brains"][0]["repair_findings"] == []
