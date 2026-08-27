from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from brain_lab.docker import DockerClient, DockerError, MAX_COMMANDS_PER_OPERATION
from brain_lab.manifests import manifest_tree
from brain_lab.process import CommandRunner


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "argv.jsonl"
    executable = tmp_path / "docker"
    executable.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
log = Path(os.environ['FAKE_DOCKER_LOG'])
with log.open('a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'import':
    while sys.stdin.buffer.read(65536):
        pass
    if os.environ.get('FAKE_DOCKER_IMPORT_FAIL'):
        sys.exit(9)
    print('sha256:imported')
elif sys.argv[1:3] == ['image', 'rm'] and os.environ.get('FAKE_DOCKER_CLEANUP_FAIL'):
    sys.exit(8)
elif sys.argv[1:3] == ['container', 'create']:
    print('container-created')
elif sys.argv[1:3] == ['image', 'inspect']:
    print(json.dumps([{'Id': 'sha256:image', 'Config': {'Labels': {}}}]))
else:
    print('{}')
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable, log


def test_docker_import_streams_manifest_and_uses_explicit_platform_and_labels(tmp_path: Path, monkeypatch):
    executable, log = _fake_docker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    root = tmp_path / "tree"
    root.mkdir()
    (root / "file.txt").write_text("content", encoding="utf-8")
    client = DockerClient(CommandRunner(), executable=str(executable))

    execution = client.import_bundle(
        root,
        manifest_tree(root),
        destination_prefix="bundle/source",
        tag="brain-lab-source:source-1",
        platform="linux/arm64",
        labels=DockerClient.labels("source", "source-1"),
        evidence_directory=tmp_path / "evidence",
    )

    assert execution.succeeded
    argv = json.loads(log.read_text().splitlines()[0])
    assert argv[:3] == ["import", "--platform", "linux/arm64"]
    assert "LABEL io.github.rob-morris.brain-lab.id=source-1" in argv
    assert argv[-2:] == ["-", "brain-lab-source:source-1"]


def test_failed_import_reports_cleanup_survivor(tmp_path: Path, monkeypatch):
    executable, log = _fake_docker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_IMPORT_FAIL", "1")
    monkeypatch.setenv("FAKE_DOCKER_CLEANUP_FAIL", "1")
    root = tmp_path / "tree"
    root.mkdir()
    (root / "file.txt").write_text("content", encoding="utf-8")
    client = DockerClient(CommandRunner(), executable=str(executable))

    with pytest.raises(DockerError) as caught:
        client.import_bundle(
            root,
            manifest_tree(root),
            destination_prefix="bundle/source",
            tag="brain-lab-source:source-1",
            platform="linux/arm64",
            labels=DockerClient.labels("source", "source-1"),
            evidence_directory=tmp_path / "evidence",
        )

    assert caught.value.execution is not None
    assert caught.value.execution.returncode == 9
    assert caught.value.cleanup_error
    assert caught.value.survivor == {"kind": "image", "id": "brain-lab-source:source-1"}


def test_docker_launch_oserror_is_normalised_to_docker_error(tmp_path: Path):
    client = DockerClient(CommandRunner(), executable=str(tmp_path / "missing-docker"))

    with pytest.raises(DockerError, match="Docker invocation failed"):
        client.verify_available(tmp_path / "evidence")


def test_declared_retry_budget_expands_the_bounded_operation_evidence_limit():
    client = DockerClient(CommandRunner())
    client.begin_operation()

    client.reserve_retry_commands(39)

    assert client.operation_command_limit == MAX_COMMANDS_PER_OPERATION + 39


def test_label_verification_refuses_lookalike_resource():
    inspect = {
        "Config": {
            "Labels": {
                "io.github.rob-morris.brain-lab.managed": "true",
                "io.github.rob-morris.brain-lab.kind": "run",
                "io.github.rob-morris.brain-lab.id": "run-other",
            }
        }
    }

    try:
        DockerClient.verify_resource_labels(inspect, "run", "run-wanted")
    except RuntimeError as exc:
        assert "refusing mutation" in str(exc)
    else:
        raise AssertionError("lookalike Docker resource was accepted")


def test_container_start_passes_the_explicit_platform(tmp_path: Path, monkeypatch):
    executable, log = _fake_docker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    client = DockerClient(CommandRunner(), executable=str(executable))

    client.start_container(
        "sha256:image",
        name="brain-lab-run-run-1",
        platform="linux/amd64",
        network="none",
        labels=DockerClient.labels("run", "run-1"),
        evidence_directory=tmp_path / "evidence",
    )

    argv = json.loads(log.read_text().splitlines()[0])
    assert argv[:8] == [
        "run",
        "--detach",
        "--name",
        "brain-lab-run-run-1",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
    ]


def test_stopped_container_creation_passes_platform_and_labels(tmp_path: Path, monkeypatch):
    executable, log = _fake_docker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    client = DockerClient(CommandRunner(), executable=str(executable))

    container_id, _ = client.create_container(
        "sha256:source",
        name="brain-lab-source-copy-attempt-1",
        platform="linux/arm64",
        labels=DockerClient.labels("attempt", "attempt-1"),
        evidence_directory=tmp_path / "evidence",
    )

    assert container_id == "container-created"
    argv = json.loads(log.read_text().splitlines()[0])
    assert argv[:7] == [
        "container",
        "create",
        "--name",
        "brain-lab-source-copy-attempt-1",
        "--platform",
        "linux/arm64",
        "--label",
    ]
    assert "io.github.rob-morris.brain-lab.id=attempt-1" in argv
    assert argv[-2:] == ["sha256:source", "/bin/true"]


def test_container_exec_can_select_root_for_scoped_ownership_repair(tmp_path: Path, monkeypatch):
    executable, log = _fake_docker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    client = DockerClient(CommandRunner(), executable=str(executable))

    client.exec(
        "container-1",
        ["chown", "-R", "10001:10001", "/home/brain/source"],
        user="root",
        evidence_directory=tmp_path / "evidence",
    )

    argv = json.loads(log.read_text().splitlines()[0])
    assert argv == [
        "exec",
        "--user",
        "root",
        "container-1",
        "chown",
        "-R",
        "10001:10001",
        "/home/brain/source",
    ]
