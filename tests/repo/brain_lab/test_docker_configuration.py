from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from brain_lab.docker import DockerClient, DockerError
from brain_lab.docker_configuration import (
    DockerConfiguration, DockerCredentialIsolationError, DockerDaemonMismatch,
    DockerEndpointError, DockerConfigurationError, PUBLIC_CONFIG,
)
from brain_lab.process import CommandRunner
from brain_lab.application import Application, HandlerResult
from brain_lab.store import StateStore
from brain_lab.scenarios import register_scenario_handlers
from brain_lab.cli import _parser, build_application
from brain_lab.process import ProcessLaunchError


@pytest.mark.parametrize(("context", "host", "expected"), [
    (None, None, "unix:///configured-desktop"),
    (None, "unix:///override", "unix:///override"),
    ("chosen", None, "unix:///context/chosen"),
])
def test_resolves_before_isolation_and_pins_endpoint(docker, tmp_path, monkeypatch, context, host, expected):
    if context:
        monkeypatch.setenv("DOCKER_CONTEXT", context)
    if host:
        monkeypatch.setenv("DOCKER_HOST", host)
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", '{"secret":"ambient"}')
    monkeypatch.setenv("BUILDX_BUILDER", "remote-builder")
    client = DockerClient(CommandRunner(), executable=str(docker))
    client.pull("ubuntu:24.04", "linux/arm64", tmp_path / "evidence")
    call = json.loads((tmp_path / "call.json").read_text())
    assert call["config"] == PUBLIC_CONFIG
    assert call["environment"]["DOCKER_HOST"] == expected
    assert "DOCKER_CONTEXT" not in call["environment"]
    assert "DOCKER_AUTH_CONFIG" not in call["environment"]
    assert "BUILDX_BUILDER" not in call["environment"]
    assert not Path(call["environment"]["DOCKER_CONFIG"]).exists()
    connection = json.loads((tmp_path / "evidence/docker-connection.json").read_text())
    assert connection["endpoint"] == expected
    assert connection["credential_mode"] == "public"
    assert connection == {
        "endpoint": expected, "daemon_id": "original-daemon", "credential_mode": "public",
        "credential_helpers": "disabled", "endpoint_pinned": True,
        "starting_context": context or "desktop-linux", "cli_version": "29.7.2", "server_version": "29.7.2",
        "starting_environment": {
            "DOCKER_AUTH_CONFIG": "<redacted>", "BUILDX_BUILDER": "<redacted>",
            **({"DOCKER_CONTEXT": context} if context else {}), **({"DOCKER_HOST": host} if host else {}),
        },
    }


@pytest.mark.parametrize(("variable", "value", "error"), [
    ("BAD_CONTEXT", "1", DockerEndpointError),
    ("DOCKER_HOST", "tcp://remote:2376", DockerEndpointError),
    ("DOCKER_TLS_VERIFY", "1", DockerEndpointError),
    ("MISMATCH", "1", DockerDaemonMismatch),
])
def test_discovery_fails_closed(docker, tmp_path, monkeypatch, variable, value, error):
    monkeypatch.setenv(variable, value)
    client = DockerClient(CommandRunner(), executable=str(docker))
    with pytest.raises(error):
        client.pull("ubuntu:24.04", "linux/arm64", tmp_path / "evidence")
    assert not (tmp_path / "call.json").exists()


def test_pin_survives_environment_change_but_daemon_change_is_rejected(docker, tmp_path, monkeypatch):
    client = DockerClient(CommandRunner(), executable=str(docker))
    client.pull("ubuntu:24.04", "linux/arm64", tmp_path / "first")
    monkeypatch.setenv("DOCKER_HOST", "unix:///changed")
    client.pull("ubuntu:24.04", "linux/arm64", tmp_path / "second")
    assert json.loads((tmp_path / "call.json").read_text())["environment"]["DOCKER_HOST"] == "unix:///configured-desktop"
    monkeypatch.setenv("MISMATCH", "1")
    with pytest.raises(DockerDaemonMismatch):
        client.pull("ubuntu:24.04", "linux/arm64", tmp_path / "third")


def test_authenticated_output_is_discarded_before_receipts(docker, tmp_path, monkeypatch):
    config = tmp_path / "auth.json"
    config.write_text(json.dumps({"auths": {"private.test": {"password": "secret-value", "username": "user"}}}))
    monkeypatch.setenv("OUTPUT", "secret-value encoded-or-transformed-secret")
    client = DockerClient(CommandRunner(), executable=str(docker), credential_config=config)
    execution = client.pull("private.test/image", "linux/arm64", tmp_path / "evidence")
    assert execution.output_redacted
    assert not execution.evidence_complete
    assert Path(execution.stdout.path).read_bytes() == b""
    assert Path(execution.stderr.path).read_bytes() == b""
    assert "secret-value" not in json.dumps(execution.to_dict())
    assert json.loads((tmp_path / "call.json").read_text())["config"]["auths"]["private.test"]["password"] == "secret-value"


@pytest.mark.parametrize("value", [
    {"credsStore": "desktop"}, {"auths": {}, "credHelpers": {}}, {"auths": {}},
    *({"auths": {"registry": credentials}} for credentials in [
        {"auth": 5}, {"auth": "%%%"}, {"auth": "dXNlcjo="}, {"auth": "OnBhc3M="},
        {"auth": "dXNlcjpwYXNz", "password": "pass"}, {"username": "user"},
        {"password": "pass"}, {"username": "user", "password": ""}, {"identitytoken": ""},
        {"identitytoken": "one", "registrytoken": "two"},
    ]),
])
def test_inline_mode_rejects_helpers_and_invalid_configuration(docker, tmp_path, value):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(value))
    client = DockerClient(CommandRunner(), executable=str(docker), credential_config=path)
    with pytest.raises(DockerCredentialIsolationError):
        client.pull("private.test/image", "linux/arm64", tmp_path / "evidence")
    assert not (tmp_path / "call.json").exists()


def test_simultaneous_endpoint_sources_are_typed_docker_errors(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "unix:///host")
    monkeypatch.setenv("DOCKER_CONTEXT", "context")
    with pytest.raises(DockerError) as caught:
        DockerClient(CommandRunner(), executable=str(docker)).pull("ubuntu", "linux/arm64", tmp_path / "evidence")
    assert isinstance(caught.value, DockerEndpointError)


def test_real_shell_boundary_is_isolated_and_persists_connection(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "secret")
    client = DockerClient(CommandRunner(), executable=str(docker))
    evidence = tmp_path / "shell"
    assert client.shell("container", evidence_directory=evidence) == 0
    call = json.loads((tmp_path / "call.json").read_text())
    assert call["config"] == PUBLIC_CONFIG
    assert "DOCKER_AUTH_CONFIG" not in call["environment"]
    assert not Path(call["environment"]["DOCKER_CONFIG"]).exists()
    assert json.loads((evidence / "docker-connection.json").read_text())["endpoint"] == "unix:///configured-desktop"
    assert json.loads((evidence / "shell.json").read_text()) == {
        "argv": [str(docker), "exec", "-it", "container", "/bin/bash"], "returncode": 0,
        "outcome": "success", "evidence_completeness": "partial", "interactive_output_retained": False,
    }


def test_scenario_keeps_one_pin_across_nested_steps(docker, tmp_path, monkeypatch):
    application = build_application(state_directory=tmp_path / "state", docker_executable=str(docker))

    def step(context, request):
        if request.get("change"):
            monkeypatch.setenv("DOCKER_HOST", "unix:///other-daemon")
        if request.get("mismatch"):
            monkeypatch.setenv("MISMATCH", "1")
        context.docker.pull("ubuntu", "linux/arm64", context.evidence_directory / "pull")
        return HandlerResult()

    application.register("test.step", step)
    result = application.dispatch("scenario.run", {"host_state": False, "steps": [
        {"operation": "test.step", "request": {}},
        {"operation": "test.step", "request": {"change": True}},
        {"operation": "test.step", "request": {"mismatch": True}},
    ]})
    steps = result.payload["steps"]
    assert [item["outcome"] for item in steps] == ["success", "success", "failure"]
    assert steps[2]["errors"][0]["type"] == "DockerDaemonMismatch"
    connections = [json.loads((application.store.evidence / item["operation_id"] / "pull/docker-connection.json").read_text()) for item in steps]
    assert connections[0] == connections[1] == connections[2]
    assert connections[0]["endpoint"] == "unix:///configured-desktop"
    assert application.docker.configuration.endpoint is None


@pytest.mark.parametrize("operation", ["pull", "build"])
def test_cli_authenticated_modes_preserve_only_safe_evidence(docker, tmp_path, monkeypatch, operation):
    secret = "raw-secret"
    transformed = "transformed-secret"
    auth = tmp_path / "credential-path.json"
    auth.write_text(json.dumps({"auths": {"registry": {"username": "user", "password": secret}}}))
    args = _parser().parse_args(["--registry-auth-config", str(auth), "base", "build"])
    application = build_application(state_directory=tmp_path / "state", docker_executable=str(docker), credential_config=args.registry_auth_config)
    monkeypatch.setenv("OUTPUT", secret + transformed)

    def execute(context, request):
        if operation == "pull":
            context.docker.pull("registry/image", "linux/arm64", context.evidence_directory / "command")
        else:
            context.docker.build("FROM registry/image", tag="test:image", platform="linux/arm64", labels={}, build_arguments={}, evidence_directory=context.evidence_directory / "command")
        return HandlerResult()

    application.register("test.auth", execute)
    result = application.dispatch("test.auth", {})
    assert result.ok and result.evidence_completeness == "partial"
    evidence = application.store.evidence / result.operation_id
    assert json.loads((evidence / "command/docker-connection.json").read_text()) == {
        "endpoint": "unix:///configured-desktop", "daemon_id": "original-daemon", "credential_mode": "inline-auth",
        "credential_helpers": "disabled", "endpoint_pinned": True, "starting_context": "desktop-linux",
        "starting_environment": {}, "cli_version": "29.7.2", "server_version": "29.7.2",
    }
    for path in application.store.root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert not any(value.encode() in data for value in (secret, transformed, str(auth)))


def test_operation_receipts_report_auth_redaction_and_typed_discovery_failure(docker, tmp_path, monkeypatch):
    credentials = tmp_path / "auth.json"
    credentials.write_text('{"auths":{"registry":{"auth":"dXNlcjpwYXNz"}}}')
    runner = CommandRunner()
    client = DockerClient(runner, executable=str(docker), credential_config=credentials)
    application = Application(
        store=StateStore(tmp_path / "state"), runner=runner, docker=client,
        tool_root=Path(__file__).resolve().parents[3] / "tools/brain-lab",
    )

    def pull(context, request):
        context.docker.pull("registry/image", "linux/arm64", context.evidence_directory / "pull")
        return HandlerResult()

    application.register("test.pull", pull)
    result = application.dispatch("test.pull", {})
    assert result.ok
    assert result.evidence_completeness == "partial"
    commands = json.loads((application.store.evidence / result.operation_id / "commands.json").read_text())
    assert commands[0]["output_redacted"] is True
    monkeypatch.setenv("BAD_CONTEXT", "1")
    failed = application.dispatch("test.pull", {})
    assert failed.errors[0]["type"] == "DockerEndpointError"
    assert failed.effect_certainty == "none"
    assert failed.evidence_completeness == "partial"


def test_successful_command_with_truncated_output_has_partial_operation_evidence(docker, tmp_path, monkeypatch):
    application = build_application(state_directory=tmp_path / "state", docker_executable=str(docker))
    monkeypatch.setenv("OUTPUT", "x" * 1000)

    def execute(context, request):
        # Discovery needs its normal bound; lower only the actual command bound.
        context.docker.configuration._resolve()
        context.runner.stream_limit = 100
        context.docker.exec("container", ["echo", "output"], evidence_directory=context.evidence_directory / "exec")
        return HandlerResult()

    application.register("test.truncated", execute)
    result = application.dispatch("test.truncated", {})
    assert result.ok
    assert result.evidence_completeness == "partial"


@pytest.mark.parametrize("target", ["config.json", "docker-connection.json", "shell.json"])
def test_configuration_filesystem_errors_keep_their_owner_and_cause(docker, tmp_path, monkeypatch, target):
    original = Path.write_text

    def failing_write(path, *args, **kwargs):
        if path.name == target:
            raise PermissionError("filesystem unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)
    client = DockerClient(CommandRunner(), executable=str(docker))
    with pytest.raises(DockerConfigurationError) as caught:
        if target == "shell.json":
            client.shell("container", evidence_directory=tmp_path / "evidence")
        else:
            client.pull("ubuntu", "linux/arm64", tmp_path / "evidence")
    assert isinstance(caught.value.__cause__, PermissionError)
    assert not caught.value.evidence_complete
    assert "invocation" not in str(caught.value)


@pytest.mark.parametrize("interactive", [False, True])
def test_process_launch_failure_is_typed_with_partial_evidence(docker, tmp_path, monkeypatch, interactive):
    import brain_lab.process as process

    original = process.subprocess.Popen

    def failing_launch(argv, *args, **kwargs):
        if argv[1] in {"pull", "exec"}:
            raise FileNotFoundError("executable disappeared")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", failing_launch)
    client = DockerClient(CommandRunner(), executable=str(docker))
    evidence = tmp_path / "evidence"
    with pytest.raises(DockerError) as caught:
        if interactive:
            client.shell("container", evidence_directory=evidence)
        else:
            client.pull("ubuntu", "linux/arm64", evidence)
    assert type(caught.value) is DockerError
    assert isinstance(caught.value.__cause__, ProcessLaunchError)
    assert isinstance(caught.value.__cause__.__cause__, FileNotFoundError)
    assert not caught.value.evidence_complete
    if interactive:
        assert json.loads((evidence / "shell.json").read_text())["returncode"] is None


def test_interrupted_shell_records_partial_failure(docker, tmp_path, monkeypatch):
    runner = CommandRunner()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runner, "interactive", interrupted)
    client = DockerClient(runner, executable=str(docker))
    with pytest.raises(DockerError) as caught:
        client.shell("container", evidence_directory=tmp_path / "shell")
    assert not caught.value.evidence_complete
    assert json.loads((tmp_path / "shell/shell.json").read_text())["outcome"] == "failure"
