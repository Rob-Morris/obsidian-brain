from __future__ import annotations

import base64
import json
import os
import platform
import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import pytest

from brain_lab.docker import DockerClient
from brain_lab.process import CommandRunner


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("BRAIN_LAB_DOCKER_CONFIGURATION_ACCEPTANCE") != "1",
        reason="set BRAIN_LAB_DOCKER_CONFIGURATION_ACCEPTANCE=1 for native Docker acceptance",
    ),
]

_HELPERS = ("desktop", "osxkeychain", "pass", "secretservice", "wincred")


def _platform() -> str:
    configured = os.environ.get("BRAIN_LAB_DOCKER_PLATFORM")
    if configured:
        return configured
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    return f"linux/{architecture}"


def _install_fail_fast_helpers(directory: Path, marker: Path) -> None:
    directory.mkdir()
    body = f"#!/bin/sh\nprintf '%s\\n' \"$0\" >> {shlex.quote(str(marker))}\nexit 97\n"
    for helper in _HELPERS:
        executable = directory / f"docker-credential-{helper}"
        executable.write_text(body, encoding="utf-8")
        executable.chmod(0o755)


def _poison_inherited_configuration(tmp_path: Path, monkeypatch) -> Path:
    marker = tmp_path / "credential-helper-invoked"
    helpers = tmp_path / "helpers"
    _install_fail_fast_helpers(helpers, marker)
    monkeypatch.setenv("PATH", f"{helpers}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("BUILDX_BUILDER", "brain-lab-missing-builder")
    monkeypatch.setenv("BUILDX_CONFIG", str(tmp_path / "missing-buildx-config"))
    monkeypatch.setenv("BUILDKIT_HOST", "tcp://brain-lab.invalid:1234")
    return marker


def _assert_connection(path: Path, mode: str) -> None:
    connection = json.loads(path.read_text(encoding="utf-8"))
    assert connection["credential_mode"] == mode
    assert connection["credential_helpers"] == "disabled"
    assert connection["endpoint_pinned"] is True
    assert connection["endpoint"].startswith("unix:///")
    assert connection["daemon_id"]
    assert connection["cli_version"]
    assert connection["server_version"]
    assert connection["starting_environment"]["BUILDX_BUILDER"] == "<redacted>"
    assert connection["starting_environment"]["BUILDX_CONFIG"] == "<redacted>"
    assert connection["starting_environment"]["BUILDKIT_HOST"] == "<redacted>"


def _sensitive_values(path: Path) -> set[bytes]:
    values: set[bytes] = {str(path).encode()}
    auths = json.loads(path.read_text(encoding="utf-8"))["auths"]
    for credentials in auths.values():
        values.update(
            value.encode()
            for value in credentials.values()
            if isinstance(value, str) and value
        )
        if credentials.get("auth"):
            decoded = base64.b64decode(credentials["auth"], validate=True)
            _, _, secret = decoded.partition(b":")
            if secret:
                values.add(secret)
    return values


def _assert_sensitive_values_absent(root: Path, values: set[bytes]) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert not any(value in content for value in values)


def _image_id(reference: str) -> str | None:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return completed.stdout.strip()
    if "No such image" in completed.stderr:
        return None
    raise RuntimeError(f"cannot snapshot Docker image {reference}: {completed.stderr.strip()}")


def _restore_image(reference: str, original_id: str | None) -> None:
    current_id = _image_id(reference)
    if current_id == original_id:
        return
    if current_id is not None:
        subprocess.run(["docker", "image", "rm", reference], check=True)
    if original_id is not None:
        subprocess.run(["docker", "image", "tag", original_id, reference], check=True)


@contextmanager
def _restore_image_references(*references: str) -> Iterator[None]:
    snapshots = {reference: _image_id(reference) for reference in references}
    primary: Exception | None = None
    try:
        yield
    except Exception as exc:
        primary = exc
    cleanup_errors: list[Exception] = []
    for reference, original_id in reversed(tuple(snapshots.items())):
        try:
            _restore_image(reference, original_id)
        except Exception as exc:
            cleanup_errors.append(RuntimeError(f"cannot restore Docker image {reference}: {exc}"))
    if cleanup_errors:
        if primary is not None:
            raise ExceptionGroup(
                "native Docker acceptance and cleanup both failed",
                [primary, *cleanup_errors],
            )
        raise ExceptionGroup("native Docker acceptance cleanup failed", cleanup_errors)
    if primary is not None:
        raise primary


def test_public_pull_and_build_do_not_invoke_helpers_or_inherit_buildx(
    tmp_path: Path, monkeypatch
) -> None:
    marker = _poison_inherited_configuration(tmp_path, monkeypatch)
    image = os.environ.get("BRAIN_LAB_PUBLIC_IMAGE", "alpine:3.22")
    if "@" in image:
        pytest.fail("BRAIN_LAB_PUBLIC_IMAGE must be a tag so its prior reference can be restored")
    tag = f"brain-lab-config-acceptance:{uuid4().hex}"
    client = DockerClient(CommandRunner())
    client.begin_operation()
    with _restore_image_references(image, tag):
        client.build(
            f"FROM {image}\nRUN true\n",
            tag=tag,
            platform=_platform(),
            labels=DockerClient.labels("acceptance", tag),
            build_arguments={},
            evidence_directory=tmp_path / "build",
        )
        client.pull(image, _platform(), tmp_path / "pull")
        _assert_connection(tmp_path / "build/docker-connection.json", "public")
        _assert_connection(tmp_path / "pull/docker-connection.json", "public")
        assert not marker.exists()


def test_authenticated_pull_and_build_use_inline_auth_without_helpers(
    tmp_path: Path, monkeypatch
) -> None:
    configured_path = os.environ.get("BRAIN_LAB_REGISTRY_AUTH_CONFIG")
    image = os.environ.get("BRAIN_LAB_AUTHENTICATED_IMAGE")
    if not configured_path or not image:
        pytest.skip(
            "set BRAIN_LAB_REGISTRY_AUTH_CONFIG and BRAIN_LAB_AUTHENTICATED_IMAGE "
            "for private-registry acceptance"
        )
    if "@" in image:
        pytest.fail("BRAIN_LAB_AUTHENTICATED_IMAGE must be a tag so its prior reference can be restored")
    credentials = Path(configured_path).resolve()
    marker = _poison_inherited_configuration(tmp_path, monkeypatch)
    tag = f"brain-lab-auth-config-acceptance:{uuid4().hex}"
    client = DockerClient(CommandRunner(), credential_config=credentials)
    client.begin_operation()
    with _restore_image_references(image, tag):
        client.build(
            f"FROM {image}\nRUN true\n",
            tag=tag,
            platform=_platform(),
            labels=DockerClient.labels("acceptance", tag),
            build_arguments={},
            evidence_directory=tmp_path / "build",
            pull=True,
        )
        client.pull(image, _platform(), tmp_path / "pull")
        _assert_connection(tmp_path / "build/docker-connection.json", "inline-auth")
        _assert_connection(tmp_path / "pull/docker-connection.json", "inline-auth")
        assert not marker.exists()
        _assert_sensitive_values_absent(tmp_path, _sensitive_values(credentials))
