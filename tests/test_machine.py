import json
import os
from pathlib import Path
import subprocess
import sys

import doctor_machine
import machine
from _common import central_venvs_root, resolve_vault_venv_python
from _machine.discovery import discover_brains, machine_registry_path, sync_machine_registry
from _machine.maintenance import inspect_machine_runtime_state, migrate_legacy_brains, prune_orphaned_runtimes
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
    assert summary["counts"]["repair_findings"] == 2
    assert not summary["healthy"]


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
    assert "repair: mcp — Brain MCP project registration state is drifted or incomplete." in human
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
    assert payload["counts"]["repair_findings"] == 2
    assert {finding["repair"]["scope"] for finding in payload["brains"][0]["repair_findings"]} == {"mcp", "registry"}


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
    assert payload["machine_registry"]["brains"] == 1
    assert payload["brains"][0]["runtime"]["status"] == "central_exact"
    assert payload["brains"][0]["repair_findings"] == []
