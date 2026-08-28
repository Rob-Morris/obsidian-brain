from __future__ import annotations

import hashlib
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import brain_lab.fixtures as fixture_module
from brain_lab.application import Application
from brain_lab.docker import DockerClient
from brain_lab.fixtures import FIXTURE_SCHEMA, register_fixture_handlers
from brain_lab.fixture_publication import publish_directory_exclusive
from brain_lab.process import CommandRunner
from brain_lab.run_state import (
    RunManifestCaptureError,
    RunManifestHelper,
    capture_run_manifest,
)
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
            "sources": [".mcp.json"],
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


@dataclass(frozen=True)
class ExecutionSpec:
    succeeded: bool = True
    timed_out: bool = False
    cancelled: bool = False
    evidence_complete: bool = True


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
        self.probe_execution = ExecutionSpec()
        self.after_manifest_execution = ExecutionSpec()

    def begin_operation(self):
        return 0

    @property
    def operation_command_limit(self):
        return 32

    def container_inspect(self, reference, evidence_directory):
        self.calls.append(("inspect", reference))
        return {
            "Id": CONTAINER_ID,
            "Name": "/brain-lab-run-fixture",
            "Image": "sha256:fixture-image",
            "State": {"Running": self.running},
            "Config": {"Labels": self.labels},
        }

    def verify_resource_labels(self, inspect, kind, resource_id):
        DockerClient.verify_resource_labels(inspect, kind, resource_id)

    def exec(self, container, argv, **kwargs):
        self.calls.append(("exec", container, list(argv), kwargs))
        manifest_execution = "--scope" in argv and "run" in argv
        if manifest_execution:
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
        execution_spec = (
            self.after_manifest_execution
            if manifest_execution and self.manifest_calls == 2
            else ExecutionSpec() if manifest_execution else self.probe_execution
        )
        return SimpleNamespace(
            succeeded=execution_spec.succeeded,
            stdout=SimpleNamespace(path=str(stdout), truncated=False),
            evidence_complete=execution_spec.evidence_complete,
            timed_out=execution_spec.timed_out,
            cancelled=execution_spec.cancelled,
        )


def _application(
    tmp_path: Path,
    docker: FixtureDocker | None = None,
    *,
    tool_root: Path = TOOL_ROOT,
) -> tuple[Application, FixtureDocker]:
    runner = CommandRunner()
    selected = docker or FixtureDocker(tmp_path)
    application = Application(
        store=StateStore(tmp_path / "state"),
        runner=runner,
        docker=selected,
        tool_root=tool_root,
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
            "name": "brain-lab-run-fixture",
            "image_id": "sha256:fixture-image",
        },
    }
    assert manifest["brain"]["core"]["version"] == "0.62.2"
    assert manifest["brain"]["core"]["tree_sha256"] == "a" * 64
    assert manifest["brain"]["mcp_config_sources"] == [".mcp.json"]
    assert manifest["skills"][0]["package_tree_sha256"] == "b" * 64
    assert manifest["skills"][0]["loader_sha256"] == _digest(docker.probe["skills"][0]["adapter"])
    assert (output / manifest["skills"][0]["loader_path"]).read_text(encoding="utf-8") == docker.probe["skills"][0]["adapter"]

    descriptor = json.loads(
        (output / "shared" / "brain-mcp-bridge.json").read_text(encoding="utf-8")
    )
    assert descriptor["run_id"] == RUN_ID
    assert descriptor["container_id"] == CONTAINER_ID
    assert descriptor["docker_executable"] == "docker"
    assert descriptor["working_directory"] == "/home/brain/vault"
    assert descriptor["command"] == "/home/brain/.brain/venvs/brain/bin/python"
    assert descriptor["args"] == [
        "-m",
        "brain_mcp.proxy",
        "/home/brain/.brain/venvs/brain/bin/python",
        "brain_mcp.server",
    ]
    executions = [call for call in docker.calls if call[0] == "exec"]
    assert executions
    assert all(call[2][0] == "/usr/bin/python3.12" for call in executions)
    manifest_executions = [call for call in executions if "--scope" in call[2]]
    assert len(manifest_executions) == 2
    for call in manifest_executions:
        assert call[2][1] == "-"
        assert call[3]["stdin"] == (TOOL_ROOT / "container" / "tree_manifest.py").read_bytes()


def test_fixture_snapshots_one_manifest_helper_for_both_captures(tmp_path: Path):
    tool_root = tmp_path / "tool"
    helper_path = tool_root / "container" / "tree_manifest.py"
    helper_path.parent.mkdir(parents=True)
    original = (TOOL_ROOT / "container" / "tree_manifest.py").read_bytes()
    helper_path.write_bytes(original)
    shutil.copy2(TOOL_ROOT / "compatibility.json", tool_root / "compatibility.json")
    shutil.copytree(TOOL_ROOT / "host_fixture", tool_root / "host_fixture")
    docker = FixtureDocker(tmp_path)
    docker.after_manifest_hook = lambda: helper_path.write_bytes(b"changed between captures\n")
    application, _selected = _application(tmp_path, docker, tool_root=tool_root)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(tmp_path / "fixture")},
    )

    assert result.ok
    manifest_calls = [
        call for call in docker.calls if call[0] == "exec" and "--scope" in call[2]
    ]
    assert [call[3]["stdin"] for call in manifest_calls] == [original, original]
    expected_sha256 = hashlib.sha256(original).hexdigest()
    assert result.evidence_bundle is not None
    evidence = application.store.evidence / result.evidence_bundle
    for name in ("01-before-run-manifest", "03-after-run-manifest"):
        helper_receipt = json.loads(
            (evidence / name / "manifest-helper.json").read_text(encoding="utf-8")
        )
        assert helper_receipt["sha256"] == expected_sha256
    run_diff = json.loads(
        (evidence / "run-manifest-diff.json").read_text(encoding="utf-8")
    )
    assert run_diff["manifest_helper_sha256"] == expected_sha256


def test_streamed_manifest_helper_executes_as_stdin_program(tmp_path: Path):
    root = tmp_path / "container-home"
    (root / "vault").mkdir(parents=True)
    (root / "vault" / "sentinel.txt").write_text("present\n", encoding="utf-8")
    helper = (TOOL_ROOT / "container" / "tree_manifest.py").read_bytes()
    completed = subprocess.run(
        [
            sys.executable,
            "-",
            "--root",
            str(root),
            "--scope",
            "run",
            "--gzip",
        ],
        input=helper,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    manifest = json.loads(gzip.decompress(completed.stdout))
    assert manifest["scope"] == "run"
    assert any(entry["path"] == "vault/sentinel.txt" for entry in manifest["entries"])


def test_missing_manifest_helper_marks_fixture_evidence_partial(tmp_path: Path):
    tool_root = tmp_path / "tool"
    tool_root.mkdir()
    shutil.copy2(TOOL_ROOT / "compatibility.json", tool_root / "compatibility.json")
    shutil.copytree(TOOL_ROOT / "host_fixture", tool_root / "host_fixture")
    application, docker = _application(tmp_path, tool_root=tool_root)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(tmp_path / "fixture")},
    )

    assert not result.ok
    assert result.evidence_completeness.value == "partial"
    assert not any(call[0] == "exec" for call in docker.calls)


def test_manifest_helper_receipt_failure_is_incomplete_evidence(tmp_path: Path):
    evidence_file = tmp_path / "not-a-directory"
    evidence_file.write_text("sentinel\n", encoding="utf-8")
    context = SimpleNamespace(
        evidence_directory=evidence_file,
        docker=SimpleNamespace(),
    )

    with pytest.raises(RunManifestCaptureError) as captured:
        capture_run_manifest(
            context,
            CONTAINER_ID,
            "manifest",
            RunManifestHelper(
                content=b"pass\n",
                sha256=hashlib.sha256(b"pass\n").hexdigest(),
            ),
        )

    assert captured.value.evidence_complete is False


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
    claude_launch = json.loads(
        (output / "clients" / "claude" / "environment.json").read_text()
    )
    assert bridge in codex
    assert claude["mcpServers"]["brain"]["command"] == bridge
    assert claude_launch["required_arguments"] == [
        "--strict-mcp-config",
        "--mcp-config",
        str(output / "clients" / "claude" / "project" / ".mcp.json"),
        "--setting-sources",
        "project",
    ]


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


def test_fixture_does_not_touch_real_or_project_client_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    real_home = tmp_path / "real-home"
    project = tmp_path / "project"
    (real_home / ".codex").mkdir(parents=True)
    project.mkdir()
    codex = real_home / ".codex" / "config.toml"
    claude = project / ".mcp.json"
    codex.write_text("sentinel-codex\n", encoding="utf-8")
    claude.write_text("sentinel-claude\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("CODEX_HOME", str(real_home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real_home / ".claude"))
    monkeypatch.chdir(project)

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


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude CLI is not installed")
def test_claude_launch_contract_excludes_user_scoped_brain_mcp(tmp_path: Path):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"
    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )
    assert result.ok

    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    user_bridge = "/user-scoped/brain-bridge"
    (isolated_home / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"brain": {"type": "stdio", "command": user_bridge, "args": []}}}
        ),
        encoding="utf-8",
    )
    launch = json.loads(
        (output / "clients" / "claude" / "environment.json").read_text(encoding="utf-8")
    )
    completed = subprocess.run(
        [shutil.which("claude"), *launch["required_arguments"], "mcp", "get", "brain"],
        cwd=launch["project_directory"],
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if key not in {"CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME"}
            },
            "HOME": str(isolated_home),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    observed = completed.stdout + completed.stderr
    assert completed.returncode == 0, observed
    project_config = json.loads(
        (output / "clients" / "claude" / "project" / ".mcp.json").read_text()
    )
    assert project_config["mcpServers"]["brain"]["command"] == str(
        output / "shared" / "brain-mcp-bridge"
    )
    assert "Scope: Project config" in observed
    assert user_bridge not in observed


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI is not installed")
def test_codex_launch_contract_selects_only_the_fixture_home(tmp_path: Path):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"
    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )
    assert result.ok

    launch = json.loads(
        (output / "clients" / "codex" / "environment.json").read_text(encoding="utf-8")
    )
    completed = subprocess.run(
        [shutil.which("codex"), "mcp", "get", "brain", "--json"],
        cwd=tmp_path,
        env={**os.environ, **launch},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    observed = completed.stdout + completed.stderr
    assert completed.returncode == 0, observed
    selected = json.loads(completed.stdout)
    assert selected["transport"]["type"] == "stdio"
    assert selected["transport"]["command"] == str(
        output / "shared" / "brain-mcp-bridge"
    )


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


def test_fixture_retains_its_partial_output_on_materialisation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"

    def fail_after_write(root: Path, **_kwargs):
        (root / "partial").write_text("inspect me\n", encoding="utf-8")
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(fixture_module, "_materialise_fixture", fail_after_write)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "partial"
    assert result.payload["cleanup_attempted"] is False
    assert result.payload["materialisation_error"] == "injected publication failure"
    assert "publication_error" not in result.payload
    partial = Path(result.payload["partial_fixture"])
    assert (partial / "partial").read_text(encoding="utf-8") == "inspect me\n"
    assert not output.exists()


def test_fixture_never_deletes_output_swapped_during_materialisation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"

    def swap_output(root: Path, **_kwargs):
        (root / "partial").write_text("inspect me\n", encoding="utf-8")
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
    assert result.payload["cleanup_attempted"] is False
    assert (output / "sentinel").read_text(encoding="utf-8") == "replacement owner\n"
    assert (Path(result.payload["partial_fixture"]) / "partial").is_file()


def test_fixture_distinguishes_exclusive_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"

    def refuse_publication(_staging: Path, _output: Path) -> None:
        raise FileExistsError("output was claimed concurrently")

    monkeypatch.setattr(fixture_module, "publish_directory_exclusive", refuse_publication)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "partial"
    assert result.payload["publication_error"] == "output was claimed concurrently"
    assert "materialisation_error" not in result.payload
    assert Path(result.payload["partial_fixture"]).is_dir()
    assert not output.exists()


def test_fixture_reports_no_effect_when_staging_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"

    def refuse_staging(_output: Path) -> Path:
        raise PermissionError("staging parent is not writable")

    monkeypatch.setattr(fixture_module, "create_staging_directory", refuse_staging)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "none"
    assert "not writable" in result.errors[0]["message"]
    assert not output.exists()


def test_fixture_preserves_probe_timeout_and_post_probe_manifest(
    tmp_path: Path,
):
    application, docker = _application(tmp_path)
    docker.probe_execution = ExecutionSpec(
        succeeded=False,
        timed_out=True,
        evidence_complete=False,
    )
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.outcome.value == "timeout"
    assert result.effect_certainty.value == "none"
    assert result.evidence_completeness.value == "partial"
    assert result.payload["run_modified"] is False
    assert docker.manifest_calls == 2
    assert not output.exists()


def test_fixture_preserves_probe_timeout_when_post_probe_manifest_also_fails(
    tmp_path: Path,
):
    application, docker = _application(tmp_path)
    docker.probe_execution = ExecutionSpec(
        succeeded=False,
        timed_out=True,
        evidence_complete=False,
    )
    docker.after_manifest_execution = ExecutionSpec(succeeded=False)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.outcome.value == "timeout"
    assert result.effect_certainty.value == "unknown"
    assert result.evidence_completeness.value == "partial"
    assert docker.manifest_calls == 2
    assert not output.exists()


def test_fixture_success_reports_partial_probe_evidence(tmp_path: Path):
    application, docker = _application(tmp_path)
    docker.probe_execution = ExecutionSpec(evidence_complete=False)
    output = tmp_path / "fixture"

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert result.ok
    assert result.evidence_completeness.value == "partial"
    assert output.is_dir()


def test_fixture_reports_known_effect_when_diff_evidence_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, _docker = _application(tmp_path)
    output = tmp_path / "fixture"
    original_write_text = Path.write_text

    def selective_failure(path: Path, *args, **kwargs):
        if path.name == "run-manifest-diff.json":
            raise OSError("evidence volume is unavailable")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", selective_failure)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(output)},
    )

    assert not result.ok
    assert result.effect_certainty.value == "none"
    assert result.evidence_completeness.value == "partial"
    assert result.payload["run_modified"] is False
    assert not output.exists()


def test_fixture_preserves_probe_timeout_when_diff_evidence_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    application, docker = _application(tmp_path)
    docker.probe_execution = ExecutionSpec(succeeded=False, timed_out=True)
    original_write_text = Path.write_text

    def selective_failure(path: Path, *args, **kwargs):
        if path.name == "run-manifest-diff.json":
            raise OSError("evidence volume is unavailable")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", selective_failure)

    result = application.dispatch(
        "fixture.create",
        {"run_id": RUN_ID, "skills": ["shaping"], "output": str(tmp_path / "fixture")},
    )

    assert not result.ok
    assert result.outcome.value == "timeout"
    assert result.effect_certainty.value == "none"
    assert result.evidence_completeness.value == "partial"


def test_exclusive_publication_never_replaces_an_existing_directory(tmp_path: Path):
    staging = tmp_path / "staging"
    output = tmp_path / "fixture"
    staging.mkdir()
    (staging / "fixture").write_text("ours\n", encoding="utf-8")
    output.mkdir()
    (output / "sentinel").write_text("other owner\n", encoding="utf-8")

    with pytest.raises(OSError):
        publish_directory_exclusive(staging, output)

    assert (output / "sentinel").read_text(encoding="utf-8") == "other owner\n"
    assert (staging / "fixture").read_text(encoding="utf-8") == "ours\n"


def test_exclusive_publication_moves_staging_to_unclaimed_output(tmp_path: Path):
    staging = tmp_path / "staging"
    output = tmp_path / "fixture"
    staging.mkdir()
    (staging / "fixture").write_text("ours\n", encoding="utf-8")

    publish_directory_exclusive(staging, output)

    assert not staging.exists()
    assert (output / "fixture").read_text(encoding="utf-8") == "ours\n"


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
    user_skill = vault / "_Config" / "Skills" / "code-review"
    shutil.copytree(core / "skills" / "code-review", user_skill)
    skill_document = user_skill / "SKILL.md"
    skill_document.write_text(
        skill_document.read_text(encoding="utf-8") + "\n<!-- user override -->\n",
        encoding="utf-8",
    )
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
    codex_config = vault / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_content = (
        "[mcp_servers.brain]\n"
        f'command = {json.dumps(str(managed_python))}\n'
        f'args = ["-m", "brain_mcp.proxy", {json.dumps(str(managed_python))}, '
        '"brain_mcp.server"]\n'
        "[mcp_servers.brain.env]\n"
        f'PYTHONPATH = {json.dumps(str(core))}\n'
    )
    codex_config.write_text(codex_content, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_ROOT / "host_fixture" / "active_brain_probe.py"),
            "--vault",
            str(vault),
            "--skill",
            "shaping",
            "--skill",
            "code-review",
            "--skill",
            "software-design-principles",
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
    assert probe["mcp"]["sources"] == [".mcp.json", ".codex/config.toml"]
    by_name = {row["name"]: row for row in probe["skills"]}
    assert set(by_name) == {"shaping", "code-review", "software-design-principles"}
    assert by_name["shaping"]["source"] == "core"
    assert by_name["shaping"]["package_path"] == ".brain-core/skills/shaping"
    assert by_name["code-review"]["source"] == "user"
    assert by_name["code-review"]["package_path"] == "_Config/Skills/code-review"
    assert "resource.read(resource=\"skill\", reference=\"code-review\")" in (
        by_name["code-review"]["adapter"]
    )
    assert by_name["code-review"]["adapter_sha256"] == hashlib.sha256(
        by_name["code-review"]["adapter"].encode("utf-8")
    ).hexdigest()
    assert by_name["software-design-principles"]["source"] == "core"

    codex_config.write_text(
        codex_content.replace('"brain_mcp.server"]', '"different.server"]'),
        encoding="utf-8",
    )
    disagreement = subprocess.run(
        completed.args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert disagreement.returncode != 0
    assert "registrations disagree" in disagreement.stderr
    codex_config.write_text(codex_content, encoding="utf-8")

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


def test_probe_adapter_handoff_falls_back_for_previous_core_loader(tmp_path: Path):
    specification = importlib.util.spec_from_file_location(
        "brain_lab_active_brain_probe",
        TOOL_ROOT / "host_fixture" / "active_brain_probe.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    calls = []

    def previous_loader(vault, name):
        calls.append((vault, name))
        return "legacy adapter"

    vault = tmp_path / "vault"
    assert module._adapter_from_snapshot(previous_loader, vault, "shaping", object()) == (
        "legacy adapter"
    )
    assert calls == [(vault, "shaping")]


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
        "working_directory": "/home/brain/vault",
        "environment": {},
        "command": "cat",
        "args": [],
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

if os.environ.get("FIXTURE_RUNNING", "true") != "true":
    print("selected container is not running", file=sys.stderr)
    raise SystemExit(3)
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

    invalid = dict(descriptor)
    invalid["schema"] = "unsupported"
    bridge.with_suffix(".json").write_text(json.dumps(invalid), encoding="utf-8")
    malformed = subprocess.run(
        [str(bridge)],
        input=b"must-not-exec",
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert malformed.returncode == 2
    assert b"unsupported schema" in malformed.stderr

    bridge.with_suffix(".json").write_text("[]", encoding="utf-8")
    wrong_root = subprocess.run(
        [str(bridge)],
        input=b"must-not-exec",
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert wrong_root.returncode == 2
    assert wrong_root.stdout == b""
    assert b"root must be an object" in wrong_root.stderr

    nul_command = dict(descriptor)
    nul_command["command"] = "cat\0unexpected"
    bridge.with_suffix(".json").write_text(json.dumps(nul_command), encoding="utf-8")
    malformed_nul = subprocess.run(
        [str(bridge)],
        input=b"must-not-exec",
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert malformed_nul.returncode == 2
    assert malformed_nul.stdout == b""
    assert b"required fields" in malformed_nul.stderr

    missing = dict(descriptor)
    missing["docker_executable"] = "definitely-missing-fixture-docker"
    bridge.with_suffix(".json").write_text(json.dumps(missing), encoding="utf-8")
    unavailable = subprocess.run(
        [str(bridge)],
        input=b"must-not-exec",
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert unavailable.returncode == 3
    assert unavailable.stdout == b""
    assert b"could not start its Docker bridge" in unavailable.stderr


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
