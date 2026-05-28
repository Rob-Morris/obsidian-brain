import json
from pathlib import Path
import subprocess
import sys

import doctor


MACHINE_SUMMARY = {
    "healthy": True,
    "tidy": True,
    "venvs_root": "/tmp/.brain/venvs",
    "stale_registry_entries": [],
    "stale_machine_registry_entries": [],
    "live_process_scan_available": True,
    "counts": {
        "brains": 1,
        "stale_registry_entries": 0,
        "brains_with_repair_findings": 0,
        "runtimes": 1,
        "orphan_candidates": 0,
    },
    "machine_registry": {
        "path": "/tmp/.config/brain/brains.json",
        "brains_count": 1,
        "blocked": False,
        "changed": False,
        "malformed_rewritten": False,
        "stale_machine_registry_entries": [],
    },
    "brains": [],
    "runtimes": [],
}

VAULT_RESULT = {
    "vault_root": "/current",
    "brain_core_version": "0.99.0",
    "checked_at": "2026-05-26T00:00:00+00:00",
    "summary": {"errors": 1, "warnings": 0, "info": 0},
    "findings": [
        {
            "check": "doctor-stub",
            "severity": "error",
            "file": "Notes/example.md",
            "message": "Vault drift",
            "repair": {
                "scope": "registry",
                "description": "Repair registry",
                "command": "python3 repair.py registry --vault /current",
            },
        }
    ],
}


def test_doctor_main_renders_human_and_json(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor,
        "collect_cli_diagnosis",
        lambda **_kwargs: {
            "version": "1.0.0",
            "binary": "/tmp/brain",
            "binary_dir": "/tmp",
            "path_ok": True,
            "launcher_python": sys.executable,
            "launcher_version": "Python 3.12.0",
            "launcher_probe_failed": False,
        },
    )
    monkeypatch.setattr(
        doctor.doctor_machine,
        "collect_machine_summary",
        lambda **_kwargs: MACHINE_SUMMARY,
    )
    monkeypatch.setattr(
        doctor,
        "collect_vault_diagnosis",
        lambda **_kwargs: {
            "in_scope": True,
            "vault_root": "/current",
            "available": True,
            "exit_code": 2,
            "message": None,
            "result": VAULT_RESULT,
        },
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "doctor.py",
            "--vault",
            "/source",
            "--current-vault",
            "/current",
            "--launcher",
            sys.executable,
            "--binary",
            "/tmp/brain",
            "--cli-version",
            "1.0.0",
        ],
    )
    assert doctor.main() == 2
    human = capsys.readouterr().out
    assert "machine diagnosis:" in human
    assert "brains:    1 discovered" in human
    assert "vault diagnosis:" in human
    assert "Vault drift" in human

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "doctor.py",
            "--vault",
            "/source",
            "--current-vault",
            "/current",
            "--launcher",
            sys.executable,
            "--binary",
            "/tmp/brain",
            "--cli-version",
            "1.0.0",
            "--json",
        ],
    )
    assert doctor.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["doctor"]["source_vault"] == "/source"
    assert payload["doctor"]["current_vault"] == "/current"
    assert payload["doctor"]["exit_code"] == 2
    assert payload["vault"]["result"]["findings"][0]["message"] == "Vault drift"


def test_collect_vault_diagnosis_rejects_unsupported_check_json(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    scripts = vault / ".brain-core" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "check.py").write_text("#!/usr/bin/env python3\n")

    monkeypatch.setattr(doctor, "find_runnable_python", lambda *_args, **_kwargs: Path(sys.executable))
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda argv, capture_output, text, timeout, check: subprocess.CompletedProcess(
            argv,
            0,
            json.dumps({"summary": {"errors": 1}, "findings": [{"message": "missing fields"}]}),
            "",
        ),
    )

    result = doctor.collect_vault_diagnosis(
        current_vault=str(vault),
        launcher_python=sys.executable,
        actionable=False,
        severity=None,
    )

    assert result["available"] is False
    assert "unsupported for composed Doctor output" in result["message"]
    assert result["exit_code"] == 1



def test_overall_exit_code_rolls_up_cli_machine_and_vault_states():
    cases = [
        (
            {
                "version": "1.0.0",
                "binary": "/tmp/brain",
                "binary_dir": "/tmp",
                "path_ok": True,
                "launcher_python": sys.executable,
                "launcher_version": "Python 3.12.0",
                "launcher_probe_failed": False,
            },
            {"healthy": True},
            {"in_scope": False, "available": False, "exit_code": 0},
            0,
        ),
        (
            {
                "version": "1.0.0",
                "binary": "/tmp/brain",
                "binary_dir": "/tmp",
                "path_ok": False,
                "launcher_python": sys.executable,
                "launcher_version": "Python 3.12.0",
                "launcher_probe_failed": False,
            },
            {"healthy": True},
            {"in_scope": False, "available": False, "exit_code": 0},
            1,
        ),
        (
            {
                "version": "1.0.0",
                "binary": "/tmp/brain",
                "binary_dir": "/tmp",
                "path_ok": True,
                "launcher_python": sys.executable,
                "launcher_version": "Python 3.12.0",
                "launcher_probe_failed": False,
            },
            {"healthy": False},
            {"in_scope": False, "available": False, "exit_code": 0},
            1,
        ),
        (
            {
                "version": "1.0.0",
                "binary": "/tmp/brain",
                "binary_dir": "/tmp",
                "path_ok": True,
                "launcher_python": sys.executable,
                "launcher_version": "Python 3.12.0",
                "launcher_probe_failed": False,
            },
            {"healthy": True},
            {"in_scope": True, "available": True, "exit_code": 2},
            2,
        ),
        (
            {
                "version": "1.0.0",
                "binary": "/tmp/brain",
                "binary_dir": "/tmp",
                "path_ok": True,
                "launcher_python": sys.executable,
                "launcher_version": "Python 3.12.0",
                "launcher_probe_failed": False,
            },
            {"healthy": True},
            {"in_scope": True, "available": True, "exit_code": 1},
            1,
        ),
        (
            {
                "version": "1.0.0",
                "binary": "/tmp/brain",
                "binary_dir": "/tmp",
                "path_ok": True,
                "launcher_python": sys.executable,
                "launcher_version": None,
                "launcher_probe_failed": True,
            },
            {"healthy": True},
            {"in_scope": True, "available": False, "exit_code": 1},
            1,
        ),
    ]

    for cli, machine, vault, expected in cases:
        assert doctor.overall_exit_code(cli=cli, machine=machine, vault=vault) == expected
