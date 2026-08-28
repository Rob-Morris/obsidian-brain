from __future__ import annotations

import hashlib
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

import brain_lab.fixtures as fixture_module
from brain_lab.application import Application
from brain_lab.docker import DockerClient
from brain_lab.fixtures import FIXTURE_SCHEMA, register_fixture_handlers
from brain_lab.process import CommandRunner
from brain_lab.store import StateStore


TOOL_ROOT = Path(__file__).resolve().parents[3] / "tools" / "brain-lab"
RUN_ID = "run-fixture"
CONTAINER_ID = "container-fixture"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _probe(loader: str = "---\nname: shaping\n---\n\nLoad the active Brain.\n") -> dict:
    return {
        "schema": "brain-lab.host-fixture-probe/1",
        "vault_path": "/home/brain/vault",
        "core": {
            "version": "0.62.2",
            "tree_sha256": "a" * 64,
            "file_count": 321,
            "total_bytes": 12345,
        },
        "mcp": {
            "source": ".mcp.json",
            "vault_path": "/home/brain/vault",
            "command": "/home/brain/.brain/venvs/brain/bin/python",
            "args": [
                "-m",
                "brain_mcp.proxy",
                "/home/brain/.brain/venvs/brain/bin/python",
                "brain_mcp.server",
            ],
            "environment": {
                "BRAIN_WORKSPACE_DIR": "/home/brain/vault",
                "PYTHONPATH": "/home/brain/vault/.brain-core",
            },
        },
        "skills": [
            {
                "name": "shaping",
                "source": "core",
                "package_path": ".brain-core/skills/shaping",
                "package_tree_sha256": "b" * 64,
                "package_files": [
                    {
                        "path": "SKILL.md",
                        "sha256": "c" * 64,
                        "size": 100,
                        "executable": False,
                    }
                ],
                "adapter": loader,
                "adapter_sha256": _digest(loader),
            }
        ],
    }


class FixtureDocker:
    executions = []

    def __init__(
        self,
        tmp_path: Path,
        *,
        running: bool = True,
        labels=None,
        executable: str = "docker",
    ):
        self.executable = executable
        self.tmp_path = tmp_path
        self.running = running
        self.labels = labels or DockerClient.labels("run", RUN_ID)
        self.calls = []
        self.probe = _probe()
        self.run_manifest = {
            "scope": "run",
            "tree_sha256": "d" * 64,
            "total_bytes": 1,
            "entries": [
                {
                    "path": "vault/.brain-core/VERSION",
                    "kind": "file",
                    "mode": 420,
                    "size": 1,
                    "sha256": "e" * 64,
                    "link_target": None,
                }
            ],
        }
        self.after_run_manifest = None
        self.after_manifest_hook = None
        self.manifest_calls = 0

    def begin_operation(self):
        return 0

    @property
    def operation_command_limit(self):
        return 32

    def container_inspect(self, reference, evidence_directory):
        self.calls.append(("inspect", reference))
        return {
            "Id": CONTAINER_ID,
            "Name": "/brain-lab-run-run-fixture",
            "Image": "sha256:fixture-image",
            "State": {"Running": self.running},
            "Config": {"Labels": self.labels},
        }

    def verify_resource_labels(self, inspect, kind, resource_id):
        DockerClient.verify_resource_labels(inspect, kind, resource_id)

    def exec(self, container, argv, **kwargs):
        self.calls.append(("exec", container, list(argv), kwargs))
        if any(item.endswith("/tree_manifest.py") for item in argv):
            self.manifest_calls += 1
            if self.manifest_calls == 2 and self.after_manifest_hook is not None:
                self.after_manifest_hook()
            manifest = (
                self.after_run_manifest
                if self.manifest_calls == 2 and self.after_run_manifest is not None
                else self.run_manifest
            )
            stdout = self.tmp_path / f"manifest-{len(self.calls)}.json.gz"
            stdout.write_bytes(
                gzip.compress(json.dumps(manifest).encode("utf-8"), mtime=0)
            )
        else:
            stdout = self.tmp_path / "probe.json"
            stdout.write_text(json.dumps(self.probe), encoding="utf-8")
        return SimpleNamespace(
            succeeded=True,
            stdout=SimpleNamespace(path=str(stdout), truncated=False),
            evidence_complete=True,
        )


def _application(tmp_path: Path, docker: FixtureDocker | None = None) -> tuple[Application, FixtureDocker]:
    runner = CommandRunner()
    selected = docker or FixtureDocker(tmp_path)
    application = Application(
        store=StateStore(tmp_path / "state"),
        runner=runner,
        docker=selected,
        tool_root=TOOL_ROOT,
    )
    register_fixture_handlers(application)
    application.store.write(
        "run",
        RUN_ID,
        {
            "container": {"id": CONTAINER_ID},
            "generation": 4,
            "status": "running",
        },
    )
    return application, selected


def test_fixture_exports_exact_active_loader_bridge_and_identity(tmp_path: Path):
    application, docker = _application(tmp_path)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert result.ok
    assert result.effect_certainty.value == "committed"
    assert result.payload["run_retained"] is True
    assert result.payload["run_modified"] is False
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == FIXTURE_SCHEMA
    assert manifest["run"] == {
        "id": RUN_ID,
        "generation": 4,
        "container": {
            "id": CONTAINER_ID,
            "name": "brain-lab-run-run-fixture",
            "image_id": "sha256:fixture-image",
        },
    }
    assert manifest["brain"]["core"]["version"] == "0.62.2"
    assert manifest["brain"]["core"]["tree_sha256"] == "a" * 64
    assert manifest["skills"][0]["package_tree_sha256"] == "b" * 64
    assert manifest["skills"][0]["loader_sha256"] == _digest(docker.probe["skills"][0]["adapter"])
    assert (output / manifest["skills"][0]["loader_path"]).read_text(encoding="utf-8") == docker.probe["skills"][0]["adapter"]

    descriptor = json.loads(
        (output / "shared" / "brain-mcp-bridge.json").read_text(encoding="utf-8")
    )
    assert descriptor["run_id"] == RUN_ID
    assert descriptor["container_id"] == CONTAINER_ID
    assert descriptor["docker_executable"] == "docker"
    assert descriptor["argv"][:5] == [
        "docker",
        "exec",
        "--interactive",
        "--workdir",
        "/home/brain/vault",
    ]
    assert descriptor["argv"][-6:] == [
        CONTAINER_ID,
        "/home/brain/.brain/venvs/brain/bin/python",
        "-m",
        "brain_mcp.proxy",
        "/home/brain/.brain/venvs/brain/bin/python",
        "brain_mcp.server",
    ]
    assert any(call[0] == "exec" for call in docker.calls)


def test_codex_and_claude_renderers_share_the_same_bridge_and_skill_root(tmp_path: Path):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert result.ok
    shared_skills = (output / "shared" / "skills").resolve()
    assert (output / "clients" / "codex" / "home" / "skills").resolve() == shared_skills
    assert (
        output / "clients" / "claude" / "project" / ".claude" / "skills"
    ).resolve() == shared_skills
    bridge = str(output / "shared" / "brain-mcp-bridge")
    codex = (output / "clients" / "codex" / "home" / "config.toml").read_text()
    claude = json.loads(
        (output / "clients" / "claude" / "project" / ".mcp.json").read_text()
    )
    assert bridge in codex
    assert claude["mcpServers"]["brain"]["command"] == bridge


def test_fixture_preserves_a_portable_nondefault_docker_executable(tmp_path: Path):
    docker = FixtureDocker(tmp_path, executable="fixture-docker")
    application, _selected = _application(tmp_path, docker)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert result.ok
    descriptor = json.loads(
        (output / "shared" / "brain-mcp-bridge.json").read_text(encoding="utf-8")
    )
    assert descriptor["docker_executable"] == "fixture-docker"
    assert descriptor["argv"][0] == "fixture-docker"


def test_fixture_resolves_a_path_valued_docker_executable(tmp_path: Path):
    wrapper = tmp_path / "bin" / "fixture-docker"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    relative_wrapper = os.path.relpath(wrapper)
    docker = FixtureDocker(tmp_path, executable=relative_wrapper)
    application, _selected = _application(tmp_path, docker)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert result.ok
    descriptor = json.loads(
        (output / "shared" / "brain-mcp-bridge.json").read_text(encoding="utf-8")
    )
    assert descriptor["docker_executable"] == str(wrapper.resolve())
    assert descriptor["argv"][0] == str(wrapper.resolve())


def test_fixture_bounds_requested_skill_count_before_docker(tmp_path: Path):
    application, docker = _application(tmp_path)

    result = application.dispatch(
        "fixture.create",
        {
            "run_id": RUN_ID,
            "skills": [f"skill-{index}" for index in range(33)],
            "output": str(tmp_path / "fixture"),
        },
    )

    assert not result.ok
    assert "at most 32" in result.errors[0]["message"]
    assert docker.calls == []


def test_fixture_does_not_touch_real_or_project_client_configuration(tmp_path: Path):
    application, _docker = _application(tmp_path)
    real_home = tmp_path / "real-home"
    project = tmp_path / "project"
    (real_home / ".codex").mkdir(parents=True)
    project.mkdir()
    codex = real_home / ".codex" / "config.toml"
    claude = project / ".mcp.json"
    codex.write_text("sentinel-codex\n", encoding="utf-8")
    claude.write_text("sentinel-claude\n", encoding="utf-8")

    result = application.dispatch(
        "fixture.create",
        {
            "run_id": RUN_ID,
            "skills": ["shaping"],
            "output": str(tmp_path / "fixture"),
        },
    )

    assert result.ok
    assert codex.read_text(encoding="utf-8") == "sentinel-codex\n"
    assert claude.read_text(encoding="utf-8") == "sentinel-claude\n"


def test_fixture_refuses_unknown_run_before_docker_or_output(tmp_path: Path):
    application, docker = _application(tmp_path)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": "run-unknown", "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "none"
    assert "unknown run resource" in result.errors[0]["message"]
    assert docker.calls == []
    assert not output.exists()


def test_fixture_refuses_stopped_or_wrongly_labelled_run_without_output(tmp_path: Path):
    for suffix, docker in (
        ("stopped", FixtureDocker(tmp_path, running=False)),
        ("labels", FixtureDocker(tmp_path, labels=DockerClient.labels("run", "run-other"))),
    ):
        application, selected = _application(tmp_path / suffix, docker)
        output = tmp_path / f"fixture-{suffix}"

        result = application.dispatch(
            "fixture.create",
            {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
        )

        assert not result.ok
        assert result.effect_certainty.value == "none"
        assert not any(call[0] == "exec" for call in selected.calls)
        assert not output.exists()


def test_fixture_refuses_preexisting_output_without_inspecting_run(tmp_path: Path):
    application, docker = _application(tmp_path)
    output = tmp_path / "fixture"
    output.mkdir()

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "none"
    assert "already exists" in result.errors[0]["message"]
    assert docker.calls == []


def test_fixture_rejects_probe_with_a_secret_environment_field(tmp_path: Path):
    application, docker = _application(tmp_path)
    docker.probe["mcp"]["environment"]["BRAIN_OPERATOR_KEY"] = "secret"
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "none"
    assert "BRAIN_OPERATOR_KEY" in result.errors[0]["message"]
    assert not output.exists()


def test_fixture_preserves_an_output_created_during_publication_race(tmp_path: Path):
    application, docker = _application(tmp_path)
    output = tmp_path / "fixture"

    def create_competing_output():
        output.mkdir()
        (output / "sentinel").write_text("other owner\n", encoding="utf-8")

    docker.after_manifest_hook = create_competing_output

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "none"
    assert (output / "sentinel").read_text(encoding="utf-8") == "other owner\n"


def test_fixture_cleanup_preserves_output_swapped_during_materialisation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"
    displaced = tmp_path / "displaced-fixture"

    def swap_output(*_args, **_kwargs):
        output.rename(displaced)
        output.mkdir()
        (output / "sentinel").write_text("replacement owner\n", encoding="utf-8")
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(fixture_module, "_materialise_fixture", swap_output)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "partial"
    assert "ownership changed" in result.payload["cleanup_error"]
    assert (output / "sentinel").read_text(encoding="utf-8") == "replacement owner\n"
    assert displaced.is_dir()


def test_fixture_refuses_publication_when_probe_changes_the_run(tmp_path: Path):
    application, docker = _application(tmp_path)
    changed = json.loads(json.dumps(docker.run_manifest))
    changed["tree_sha256"] = "f" * 64
    changed["entries"][0]["sha256"] = "0" * 64
    docker.after_run_manifest = changed
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "partial"
    assert result.payload["run_modified"] is True
    assert result.payload["run_manifest_diff"]["summary"]["changed"] == 1
    assert result.payload["run_manifest_diff"]["changed"][0]["path"] == (
        "vault/.brain-core/VERSION"
    )
    assert not output.exists()


def test_streamed_probe_generates_loader_from_the_selected_brain(tmp_path: Path):
    vault = tmp_path / "vault"
    core = vault / ".brain-core"
    shutil.copytree(Path("src/brain-core"), core)
    managed_python = Path(sys.executable).resolve()
    (vault / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "brain": {
                        "command": str(managed_python),
                        "args": [
                            "-m",
                            "brain_mcp.proxy",
                            str(managed_python),
                            "brain_mcp.server",
                        ],
                        "env": {"PYTHONPATH": str(core)},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_ROOT / "host_fixture" / "active_brain_probe.py"),
            "--vault",
            str(vault),
            "--skill",
            "shaping",
            "--tree-manifest",
            str(TOOL_ROOT / "container" / "tree_manifest.py"),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )

    probe = json.loads(completed.stdout)
    assert probe["core"]["version"] == (core / "VERSION").read_text().strip()
    assert probe["mcp"]["command"] == str(managed_python)
    assert probe["skills"][0]["source"] == "core"
    assert probe["skills"][0]["package_path"] == ".brain-core/skills/shaping"
    assert "resource.read(resource=\"skill\", reference=\"shaping\")" in probe["skills"][0]["adapter"]

    secret = "must-not-enter-evidence"
    config = json.loads((vault / ".mcp.json").read_text(encoding="utf-8"))
    config["mcpServers"]["brain"]["env"]["BRAIN_OPERATOR_KEY"] = secret
    (vault / ".mcp.json").write_text(json.dumps(config), encoding="utf-8")
    rejected = subprocess.run(
        completed.args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert rejected.returncode != 0
    assert secret not in rejected.stdout
    assert secret not in rejected.stderr


def test_bridge_preserves_stdio_and_fails_closed_for_a_stopped_run(tmp_path: Path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    bridge = fixture / "brain-mcp-bridge"
    shutil.copyfile(TOOL_ROOT / "host_fixture" / "brain_mcp_bridge.py", bridge)
    bridge.chmod(0o755)
    descriptor = {
        "schema": "brain-lab.host-fixture-bridge/1",
        "run_id": RUN_ID,
        "container_id": CONTAINER_ID,
        "docker_executable": "fixture-docker",
        "argv": ["fixture-docker", "exec", "--interactive", CONTAINER_ID, "cat"],
    }
    bridge.with_suffix(".json").write_text(json.dumps(descriptor), encoding="utf-8")

    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    wrapper = binary_dir / "fixture-docker"
    wrapper.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

if sys.argv[1:3] == ["container", "inspect"]:
    print("true" if os.environ.get("FIXTURE_RUNNING", "true") == "true" else "false")
    raise SystemExit(0)
Path(os.environ["FIXTURE_RECORD"]).write_text(json.dumps(sys.argv[1:]))
sys.stdout.buffer.write(sys.stdin.buffer.read())
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    record = tmp_path / "docker-argv.json"
    environment = {
        **os.environ,
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "FIXTURE_RECORD": str(record),
    }

    connected = subprocess.run(
        [str(bridge)],
        input=b"mcp-stdio",
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )

    assert connected.stdout == b"mcp-stdio"
    assert CONTAINER_ID in json.loads(record.read_text(encoding="utf-8"))

    stopped = subprocess.run(
        [str(bridge)],
        input=b"must-not-fallback",
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**environment, "FIXTURE_RUNNING": "false"},
    )
    assert stopped.returncode == 3
    assert stopped.stdout == b""
    assert b"not running" in stopped.stderr


@pytest.mark.skipif(
    not (
        os.environ.get("BRAIN_LAB_HOST_FIXTURE_RUN_ID")
        and os.environ.get("BRAIN_LAB_HOST_FIXTURE_STATE_DIR")
    ),
    reason="requires an explicitly selected retained Brain Lab run and state directory",
)
def test_live_fixture_bridge_lists_active_brain_tools(tmp_path: Path):
    run_id = os.environ["BRAIN_LAB_HOST_FIXTURE_RUN_ID"]
    state_dir = os.environ["BRAIN_LAB_HOST_FIXTURE_STATE_DIR"]
    output = tmp_path / "fixture"
    created = subprocess.run(
        [
            str(TOOL_ROOT / "brain-lab"),
            "--state-dir",
            state_dir,
            "--json",
            "fixture",
            "create",
            "--request-json",
            json.dumps(
                {"run_id": run_id, "skills": ["shaping"], "output": str(output)}
            ),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert json.loads(created.stdout)["outcome"] == "success"

    probed = subprocess.run(
        [
            sys.executable,
            str(TOOL_ROOT / "container" / "mcp_probe.py"),
            "--vault",
            str(output / "clients" / "claude" / "project"),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert json.loads(probed.stdout)["tool_count"] > 0
