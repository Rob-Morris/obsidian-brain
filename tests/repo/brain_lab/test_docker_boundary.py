from __future__ import annotations

import json
import os
import hashlib
import shutil
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from brain_lab.docker import DockerClient, DockerError, MAX_COMMANDS_PER_OPERATION
from brain_lab.docker_configuration import DockerEndpointError
from brain_lab.manifests import manifest_tree
from brain_lab.process import CommandRunner, ProcessExecution, StreamReceipt
from conftest import write_fake_docker


pytestmark = pytest.mark.usefixtures("isolate_fake_docker_environment")


def test_fake_docker_uses_test_python_without_ambient_endpoint():
    assert Path(shutil.which("python3")).resolve() == Path(sys.executable).resolve()
    assert not any(key.startswith(("DOCKER_", "BUILDX_", "BUILDKIT_")) for key in os.environ)


@pytest.fixture
def argument_client(tmp_path, monkeypatch):
    """Only argument construction is under test; wire contracts have real-process tests below."""
    client = DockerClient(CommandRunner(), executable="docker-test")
    monkeypatch.setattr(client.configuration, "environment", lambda *args, **kwargs: nullcontext({}))
    output = b"container-created\n"
    stdout = tmp_path / "stdout.log"
    stdout.write_bytes(output)
    receipt = StreamReceipt(str(stdout), len(output), len(output), hashlib.sha256(output).hexdigest(), False)
    empty = StreamReceipt(str(tmp_path / "stderr.log"), 0, 0, hashlib.sha256(b"").hexdigest(), False)
    execute = Mock(return_value=ProcessExecution(("docker-test",), 0, False, False, 0, receipt, empty))
    monkeypatch.setattr(client.runner, "run", execute)
    return client, execute


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "argv.jsonl"
    executable = write_fake_docker(tmp_path,
        """
log = Path(os.environ['FAKE_DOCKER_LOG'])
with log.open('a') as handle:
    handle.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'import':
    import tarfile
    files = {}
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
        for member in archive:
            if member.isfile():
                files[member.name] = archive.extractfile(member).read().decode('utf-8')
    (log.parent / 'imported.json').write_text(json.dumps(files))
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
    )
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
    assert json.loads((tmp_path / "imported.json").read_text()) == {"bundle/source/file.txt": "content"}


def test_docker_build_can_force_registry_resolution(tmp_path: Path, argument_client):
    client, execute = argument_client

    client.build(
        "FROM private.example/base:1\n",
        tag="brain-lab-test:forced-pull",
        platform="linux/arm64",
        labels={},
        build_arguments={},
        evidence_directory=tmp_path / "evidence",
        pull=True,
    )

    execute.assert_called_once()
    argv = execute.call_args.args[0][1:]
    assert argv[:8] == [
        "build",
        "--platform",
        "linux/arm64",
        "--file",
        "-",
        "--tag",
        "brain-lab-test:forced-pull",
        "--pull",
    ]


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

    with pytest.raises(DockerEndpointError, match="Docker invocation failed"):
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


@pytest.mark.parametrize(
    ("kind", "resource_id", "expected"),
    [
        ("run", "run-1", "brain-lab-run-1"),
        ("attempt", "attempt-1", "brain-lab-attempt-1"),
        ("source-copy", "attempt-1", "brain-lab-source-copy-attempt-1"),
    ],
)
def test_container_names_do_not_repeat_the_resource_kind(
    kind: str, resource_id: str, expected: str
):
    assert DockerClient.deterministic_container_name(kind, resource_id) == expected


def test_container_start_passes_the_explicit_platform(tmp_path: Path, argument_client):
    client, execute = argument_client

    client.start_container(
        "sha256:image",
        name="brain-lab-run-1",
        platform="linux/amd64",
        network="none",
        labels=DockerClient.labels("run", "run-1"),
        evidence_directory=tmp_path / "evidence",
    )

    execute.assert_called_once()
    argv = execute.call_args.args[0][1:]
    assert argv[:8] == [
        "run",
        "--detach",
        "--name",
        "brain-lab-run-1",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
    ]


def test_stopped_container_creation_passes_platform_and_labels(tmp_path: Path, argument_client):
    client, execute = argument_client

    container_id, _ = client.create_container(
        "sha256:source",
        name="brain-lab-source-copy-attempt-1",
        platform="linux/arm64",
        labels=DockerClient.labels("attempt", "attempt-1"),
        evidence_directory=tmp_path / "evidence",
    )

    assert container_id == "container-created"
    execute.assert_called_once()
    argv = execute.call_args.args[0][1:]
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


def test_container_exec_can_select_root_for_scoped_ownership_repair(tmp_path: Path, argument_client):
    client, execute = argument_client

    client.exec(
        "container-1",
        ["chown", "-R", "10001:10001", "/home/brain/source"],
        user="root",
        evidence_directory=tmp_path / "evidence",
    )

    execute.assert_called_once()
    argv = execute.call_args.args[0][1:]
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
