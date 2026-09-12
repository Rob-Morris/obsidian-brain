from __future__ import annotations

import json
import gzip
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import time

import pytest

from brain_lab.compatibility import CompatibilityManifest
from brain_lab.container_contract import CONTAINER_PYTHON
from brain_lab.manifests import normalised_core_tree


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_ROOT = REPO_ROOT / "tools" / "brain-lab"


def test_current_repository_version_has_an_exact_compatibility_owner():
    version = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()
    adapter = CompatibilityManifest(TOOL_ROOT / "compatibility.json").select(version)

    assert adapter.adapter_id == "brain-0.55-0.62"
    assert any(gate.gate_id == "session" for gate in adapter.health)
    session = next(gate for gate in adapter.health if gate.gate_id == "session")
    assert session.command[:3] == ("brain", "session", "start")


def test_rehydration_adapters_do_not_use_upgrade_or_definition_sync():
    manifest = CompatibilityManifest(TOOL_ROOT / "compatibility.json")

    for adapter in manifest.adapters:
        joined = " ".join(part for command in adapter.rehydrate for part in command)
        assert "upgrade.py" not in joined
        assert "sync_definitions" not in joined


def test_historical_dependency_pin_helper_accepts_only_exact_mcp_versions(tmp_path: Path):
    helper = TOOL_ROOT / "container" / "pin_mcp_requirement.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--vault",
            str(tmp_path),
            "--requirement",
            "mcp<2",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "only an exact mcp==VERSION" in completed.stderr


def test_acceptance_matrix_has_unique_executable_evidence_owners():
    matrix = json.loads((TOOL_ROOT / "acceptance-matrix.json").read_text(encoding="utf-8"))
    rows = matrix["rows"]

    assert matrix["schema"] == "brain-lab.acceptance-matrix/2"
    assert len({row["id"] for row in rows}) == len(rows) == 16
    for row in rows:
        assert all(row[field] for field in ("setup", "command", "predicate", "evidence", "owner"))
    targets = matrix["verification_targets"]
    assert set(targets) == {"test-brain-lab", "test-brain-lab-docker"}
    for target in targets.values():
        paths = target.get("paths", [target.get("path")])
        assert all(
            path is not None
            and ((TOOL_ROOT / path).exists() or (REPO_ROOT / path).exists())
            for path in paths
        )
    docker_target = targets["test-brain-lab-docker"]
    automated = {row["id"] for row in rows if row["owner"] == "automated-docker"}
    assert automated == set(docker_target["covers"])
    assert docker_target["kind"] == "docker-scenario-group"
    assert docker_target["paths"] == [
        "scenarios/current-template.json",
        "scenarios/historical-upgrade.json",
    ]
    scenarios = [
        json.loads((TOOL_ROOT / path).read_text(encoding="utf-8"))
        for path in docker_target["paths"]
    ]
    scenario = scenarios[0]
    operations = {step["operation"] for step in scenario["steps"]}
    assert {"baseline.prepare", "run.copy-in", "run.exec", "run.recreate"} <= operations
    assert scenario["host_state"] is not False
    manifest_commands = [
        step["request"]["argv"]
        for step in scenario["steps"]
        if step["operation"] == "run.exec"
        and any(argument.endswith("/tree_manifest.py") for argument in step["request"]["argv"])
    ]
    assert manifest_commands
    assert all(command[0] == CONTAINER_PYTHON for command in manifest_commands)
    historical = scenarios[1]
    historical_operations = [step["operation"] for step in historical["steps"]]
    assert historical_operations.count("source.capture") == 2
    assert "baseline.prepare" in historical_operations
    acceptance = next(
        step
        for step in historical["steps"]
        if step["operation"] == "run.exec"
        and any(
            argument.endswith("/historical_upgrade_acceptance.py")
            for argument in step["request"]["argv"]
        )
    )
    assert acceptance["request"]["argv"][0] == CONTAINER_PYTHON
    assert acceptance["request"]["argv"][-2:] == [
        "--historical-version",
        "0.53.5",
    ]
    assert historical["steps"][1]["request"]["ref"] == (
        "7bf6db30efc9e132aa84755bc6f448d575fdf85f"
    )
    assert historical["host_state"] is not False


def test_historical_upgrade_delta_uses_stable_check_severity_and_path_identity():
    helper = runpy.run_path(
        str(TOOL_ROOT / "container" / "historical_upgrade_acceptance.py"),
        run_name="historical_upgrade_acceptance_test",
    )
    before = [
        {
            "check": "living_key_fields",
            "severity": "error",
            "file": "Designs/Inherited.md",
            "message": "old wording",
        }
    ]
    after = [
        {
            "check": "living_key_fields",
            "severity": "error",
            "file": "Designs/Inherited.md",
            "message": "new wording",
        },
        {
            "check": "new_check",
            "severity": "warning",
            "file": None,
            "message": "new finding",
        },
    ]

    delta = helper["finding_delta"](before, after)

    assert delta["retained"] == [
        ("error", "living_key_fields", "Designs/Inherited.md")
    ]
    assert delta["added"] == [("warning", "new_check", "")]


def test_historical_upgrade_portable_manifest_excludes_only_local_runtime_state(
    tmp_path: Path,
):
    helper = runpy.run_path(
        str(TOOL_ROOT / "container" / "historical_upgrade_acceptance.py"),
        run_name="historical_upgrade_manifest_test",
    )
    portable = tmp_path / "Notes" / "User.md"
    portable.parent.mkdir()
    portable.write_text("user state", encoding="utf-8")
    local = tmp_path / ".brain" / "local" / "runtime.json"
    local.parent.mkdir(parents=True)
    local.write_text("runtime state", encoding="utf-8")

    manifest = helper["_portable_manifest"](tmp_path)

    assert "Notes/User.md" in manifest
    assert ".brain/local/runtime.json" not in manifest


def test_historical_upgrade_runner_stops_output_above_its_bound(tmp_path: Path):
    helper = runpy.run_path(
        str(TOOL_ROOT / "container" / "historical_upgrade_acceptance.py"),
        run_name="historical_upgrade_bound_test",
    )
    run = helper["_run"]
    run.__globals__["MAX_CAPTURE_BYTES"] = 32

    with pytest.raises(helper["AcceptanceFailure"], match="stdout exceeded"):
        run(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 64)"],
            cwd=tmp_path,
        )


def test_historical_upgrade_runner_kills_descendants_holding_output_pipe(tmp_path: Path):
    helper = runpy.run_path(
        str(TOOL_ROOT / "container" / "historical_upgrade_acceptance.py"),
        run_name="historical_upgrade_descendant_test",
    )
    run = helper["_run"]
    run.__globals__["MAX_CAPTURE_BYTES"] = 32
    child_pid = tmp_path / "child.pid"
    survivor = tmp_path / "survived"
    child_code = (
        "import pathlib,signal,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "time.sleep(0.5);"
        f"pathlib.Path({str(survivor)!r}).write_text('survived');"
        "time.sleep(60)"
    )
    leader_code = (
        "import pathlib,subprocess,sys;"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid));"
        "sys.stdout.write('x'*64);sys.stdout.flush()"
    )

    try:
        with pytest.raises(helper["AcceptanceFailure"], match="stdout exceeded"):
            run([sys.executable, "-c", leader_code], cwd=tmp_path)
        time.sleep(0.7)
        assert not survivor.exists()
    finally:
        if child_pid.exists():
            try:
                os.kill(int(child_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_package_input_recorder_captures_repository_content_and_package_versions(tmp_path: Path):
    root = tmp_path / "root"
    sources = root / "etc" / "apt" / "sources.list.d"
    sources.mkdir(parents=True)
    (sources / "ubuntu.sources").write_text("URIs: https://archive.ubuntu.com/ubuntu\n")
    status = root / "var" / "lib" / "dpkg" / "status"
    status.parent.mkdir(parents=True)
    status.write_text(
        "Package: bash\nStatus: install ok installed\nArchitecture: arm64\nVersion: 1.2.3\n\n"
    )
    script = TOOL_ROOT / "container" / "package_inputs.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["repositories"][0]["path"] == "/etc/apt/sources.list.d/ubuntu.sources"
    assert payload["repositories"][0]["content"].startswith("URIs:")
    assert payload["packages"] == [
        {"name": "bash", "version": "1.2.3", "architecture": "arm64"}
    ]


def test_prepared_manifest_normalises_only_declared_transient_state(tmp_path: Path):
    root = tmp_path / "home"
    colours = root / "vault" / ".obsidian" / "snippets" / "brain-folder-colours.css"
    colours.parent.mkdir(parents=True)
    colours.write_text("header\n   Generated: 2026-08-27 01:55\nbody\n")
    lock = root / ".config" / "brain" / "vaults.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("one")
    outcomes = root / ".local" / "state" / "brain" / "command-outcomes"
    outcomes.mkdir(parents=True)
    (outcomes / "first.json").write_text("one")
    stable = root / "vault" / "Designs" / "Stable.md"
    stable.parent.mkdir(parents=True)
    stable.write_text("stable")

    script = TOOL_ROOT / "container" / "tree_manifest.py"

    def capture():
        completed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--scope", "prepared"],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    before = capture()
    colours.write_text("header\n   Generated: 2026-08-27 02:00\nbody\n")
    lock.write_text("two")
    (outcomes / "first.json").unlink()
    (outcomes / "second.json").write_text("two")
    after = capture()

    assert before["tree_sha256"] == after["tree_sha256"]
    colour_entry = next(item for item in after["entries"] if item["path"].endswith("brain-folder-colours.css"))
    assert colour_entry["normalisation"] == "generated-timestamp"


def test_run_manifest_includes_generated_vault_state(tmp_path: Path):
    root = tmp_path / "home"
    local = root / "vault" / ".brain" / "local"
    local.mkdir(parents=True)
    script = TOOL_ROOT / "container" / "tree_manifest.py"

    def capture(scope: str):
        completed = subprocess.run(
            [sys.executable, str(script), "--root", str(root), "--scope", scope],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    prepared_before = capture("prepared")
    run_before = capture("run")
    (local / "mutation").write_text("changed\n")

    assert capture("prepared")["tree_sha256"] == prepared_before["tree_sha256"]
    assert capture("run")["tree_sha256"] != run_before["tree_sha256"]


def test_container_manifest_has_deterministic_compressed_transport(tmp_path: Path):
    root = tmp_path / "home"
    root.mkdir()
    (root / "note.md").write_text("content\n")
    script = TOOL_ROOT / "container" / "tree_manifest.py"
    command = [
        sys.executable,
        str(script),
        "--root",
        str(root),
        "--scope",
        "run",
        "--gzip",
    ]

    first = subprocess.run(command, check=True, capture_output=True).stdout
    second = subprocess.run(command, check=True, capture_output=True).stdout

    assert first == second
    assert json.loads(gzip.decompress(first))["entries"][0]["path"] == "note.md"


def test_container_core_manifest_matches_host_manifest_wire_shape(tmp_path: Path):
    vault = tmp_path / "vault"
    core = vault / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.62.0\n")
    (core / "module.py").write_text("value = 1\n")
    scripts = core / "scripts"
    scripts.mkdir()
    (scripts / "upgrade.py").write_text("distribution-only bootstrap\n")
    (core / "relative-link").symlink_to("module.py")

    script = TOOL_ROOT / "container" / "tree_manifest.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--root", str(vault), "--scope", "core"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout)["tree_sha256"] == normalised_core_tree(core).tree_sha256


def test_restore_seed_paths_reinstates_only_declared_portable_files(tmp_path: Path):
    seed = tmp_path / "seed"
    vault = tmp_path / "vault"
    seed.mkdir()
    vault.mkdir()
    (seed / "Agents.md").write_text("original\n")
    (vault / "Agents.md").write_text("changed\n")
    (vault / "generated.md").write_text("generated\n")
    untouched = vault / "note.md"
    untouched.write_text("portable\n")

    script = TOOL_ROOT / "container" / "restore_seed_paths.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--seed",
            str(seed),
            "--vault",
            str(vault),
            "--path",
            "Agents.md",
            "--path",
            "generated.md",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (vault / "Agents.md").read_text() == "original\n"
    assert not (vault / "generated.md").exists()
    assert untouched.read_text() == "portable\n"
    assert json.loads(completed.stdout) == {
        "restored": [
            {"action": "restored-file", "path": "Agents.md"},
            {"action": "removed-generated", "path": "generated.md"},
        ]
    }


def test_restore_seed_paths_rejects_escaping_path(tmp_path: Path):
    seed = tmp_path / "seed"
    vault = tmp_path / "vault"
    seed.mkdir()
    vault.mkdir()
    script = TOOL_ROOT / "container" / "restore_seed_paths.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--seed",
            str(seed),
            "--vault",
            str(vault),
            "--path",
            "../outside",
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "contained relative path" in completed.stderr


def test_clear_imported_state_removes_machine_state_and_preserves_unrelated_mcp(tmp_path: Path):
    vault = tmp_path / "vault"
    local = vault / ".brain" / "local"
    local.mkdir(parents=True)
    (local / "host-state.json").write_text("/Users/example\n")
    (vault / ".codex").mkdir()
    (vault / ".claude").mkdir()
    (vault / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "brain": {"command": "/Users/example/python"},
                    "undertask": {"command": "/opt/undertask"},
                }
            }
        )
    )
    note = vault / "note.md"
    note.write_text("portable\n")
    script = TOOL_ROOT / "container" / "clear_imported_state.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--vault", str(vault)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert not local.exists()
    assert not (vault / ".codex").exists()
    assert not (vault / ".claude").exists()
    assert note.read_text() == "portable\n"
    assert json.loads((vault / ".mcp.json").read_text()) == {
        "mcpServers": {"undertask": {"command": "/opt/undertask"}}
    }
    assert json.loads(completed.stdout) == {
        "mcp_json": "brain-server-removed",
        "removed": {".brain/local": True, ".claude": True, ".codex": True},
    }


def test_active_path_probe_scopes_mcp_paths_to_the_brain_server(tmp_path: Path):
    script = TOOL_ROOT / "container" / "active_path_probe.py"
    inspect_brain_mcp = runpy.run_path(str(script))["inspect_brain_mcp"]
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "undertask": {"command": "/Users/example/undertask"},
                    "brain": {"command": "/home/brain/runtime/python"},
                }
            }
        )
    )

    assert inspect_brain_mcp(config) == []

    config.write_text(json.dumps({"mcpServers": {"brain": {"command": "/Users/example/python"}}}))
    violations = inspect_brain_mcp(config)
    assert len(violations) == 1
    assert violations[0]["scope"] == "mcpServers.brain"


def test_mcp_probe_uses_codex_brain_server_not_unrelated_claude_server(tmp_path: Path):
    script = TOOL_ROOT / "container" / "mcp_probe.py"
    load_server = runpy.run_path(str(script))["_load_server"]
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"undertask": {"command": "/Users/example/undertask"}}})
    )
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text(
        '[mcp_servers.brain]\ncommand = "/usr/bin/python3.12"\nargs = ["server.py"]\n'
        '[mcp_servers.brain.env]\nBRAIN_VAULT_ROOT = "/home/brain/vault"\n'
    )

    argv, environment = load_server(tmp_path)

    assert argv == ["/usr/bin/python3.12", "server.py"]
    assert environment["BRAIN_VAULT_ROOT"] == "/home/brain/vault"


def test_mcp_probe_validates_revision_appropriate_read_only_contracts():
    helper = runpy.run_path(str(TOOL_ROOT / "container" / "mcp_probe.py"))

    assert helper["_probe_request"]("legacy") == ("brain_init", {})
    helper["_validate_probe_payload"](
        "legacy",
        {"version": "1", "readiness": "ready", "warmup_state": "complete"},
    )
    assert helper["_probe_request"]("canonical") == (
        "command.list",
        {"dependency_tier": "portable", "page_size": 1},
    )
    helper["_validate_probe_payload"](
        "canonical",
        {"command": "command.list", "ok": True, "result": {}},
    )


def test_mcp_probe_extracts_legacy_json_text_and_canonical_structured_content():
    payload = runpy.run_path(str(TOOL_ROOT / "container" / "mcp_probe.py"))[
        "_tool_call_payload"
    ]

    assert payload(
        {
            "content": [
                {
                    "type": "text",
                    "text": '{"version":"1","readiness":"ready"}',
                }
            ]
        }
    ) == {"version": "1", "readiness": "ready"}
    assert payload(
        {
            "structuredContent": {
                "command": "command.list",
                "ok": True,
            }
        }
    ) == {"command": "command.list", "ok": True}
