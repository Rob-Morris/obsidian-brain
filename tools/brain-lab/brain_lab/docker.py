from __future__ import annotations

import json
import hashlib
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Mapping, Sequence

from .manifests import TreeManifest
from .process import CommandRunner, ProcessExecution


LABEL_PREFIX = "io.github.rob-morris.brain-lab"
MANAGED_LABEL = f"{LABEL_PREFIX}.managed"
KIND_LABEL = f"{LABEL_PREFIX}.kind"
ID_LABEL = f"{LABEL_PREFIX}.id"
IMPORTED_LABEL = f"{LABEL_PREFIX}.imported-vault"
MAX_COMMANDS_PER_OPERATION = 32


class DockerError(RuntimeError):
    def __init__(
        self,
        message: str,
        execution: ProcessExecution | None = None,
        *,
        cleanup_error: str | None = None,
        survivor: Mapping[str, str] | None = None,
    ):
        super().__init__(message)
        self.execution = execution
        self.cleanup_error = cleanup_error
        self.survivor = survivor


class _HashingReader:
    def __init__(self, source: BinaryIO):
        self.source = source
        self.digest = hashlib.sha256()
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(size)
        self.digest.update(chunk)
        self.total += len(chunk)
        return chunk


class DockerClient:
    def __init__(self, runner: CommandRunner, *, executable: str = "docker"):
        self.runner = runner
        self.executable = executable
        self.executions: list[ProcessExecution] = []
        self._operation_command_count = 0
        self._operation_command_limit = MAX_COMMANDS_PER_OPERATION

    def begin_operation(self) -> int:
        self._operation_command_count = 0
        self._operation_command_limit = MAX_COMMANDS_PER_OPERATION
        return len(self.executions)

    @property
    def operation_command_limit(self) -> int:
        return self._operation_command_limit

    def reserve_retry_commands(self, additional_commands: int) -> None:
        if additional_commands < 0:
            raise ValueError("additional Docker command reservation cannot be negative")
        self._operation_command_limit += additional_commands

    @staticmethod
    def labels(kind: str, resource_id: str, *, imported: bool = False) -> dict[str, str]:
        return {
            MANAGED_LABEL: "true",
            KIND_LABEL: kind,
            ID_LABEL: resource_id,
            IMPORTED_LABEL: "true" if imported else "false",
        }

    @staticmethod
    def deterministic_tag(kind: str, resource_id: str) -> str:
        return f"brain-lab-{kind}:{resource_id}"

    @staticmethod
    def deterministic_container_name(kind: str, resource_id: str) -> str:
        return f"brain-lab-{kind}-{resource_id}"

    def _execute(
        self,
        arguments: Sequence[str],
        *,
        evidence_directory: Path,
        timeout_seconds: float = 300,
        stdin: bytes | None = None,
        stdin_writer=None,
    ) -> ProcessExecution:
        self._operation_command_count += 1
        if self._operation_command_count > self._operation_command_limit:
            raise DockerError(
                f"operation exceeded the {self._operation_command_limit}-command evidence bundle bound"
            )
        try:
            execution = self.runner.run(
                [self.executable, *arguments],
                evidence_directory=evidence_directory,
                timeout_seconds=timeout_seconds,
                stdin=stdin,
                stdin_writer=stdin_writer,
            )
        except OSError as exc:
            raise DockerError(f"Docker invocation failed before completion: {exc}") from exc
        self.executions.append(execution)
        return execution

    @staticmethod
    def _stdout(execution: ProcessExecution) -> str:
        if execution.stdout.truncated:
            raise DockerError("Docker JSON output exceeded its retention bound", execution)
        return Path(execution.stdout.path).read_text(encoding="utf-8")

    @classmethod
    def _require_success(cls, execution: ProcessExecution, action: str) -> str:
        if not execution.succeeded:
            raise DockerError(f"Docker {action} failed with exit code {execution.returncode}", execution)
        return cls._stdout(execution)

    def verify_available(self, evidence_directory: Path, *, timeout_seconds: float = 30) -> dict[str, Any]:
        execution = self._execute(
            ["version", "--format", "{{json .}}"],
            evidence_directory=evidence_directory,
            timeout_seconds=timeout_seconds,
        )
        payload = json.loads(self._require_success(execution, "version probe"))
        if not payload.get("Server"):
            raise DockerError("Docker daemon did not return server information", execution)
        return payload

    def pull(self, image: str, platform: str, evidence_directory: Path, *, timeout_seconds: float = 900) -> ProcessExecution:
        execution = self._execute(
            ["pull", "--platform", platform, image],
            evidence_directory=evidence_directory,
            timeout_seconds=timeout_seconds,
        )
        self._require_success(execution, f"pull of {image}")
        return execution

    def image_inspect(self, reference: str, evidence_directory: Path) -> dict[str, Any]:
        execution = self._execute(
            ["image", "inspect", reference], evidence_directory=evidence_directory, timeout_seconds=60
        )
        values = json.loads(self._require_success(execution, f"inspection of image {reference}"))
        if not isinstance(values, list) or len(values) != 1:
            raise DockerError(f"Docker returned an unexpected image inspection for {reference}", execution)
        return values[0]

    def container_inspect(self, reference: str, evidence_directory: Path) -> dict[str, Any]:
        execution = self._execute(
            ["container", "inspect", reference], evidence_directory=evidence_directory, timeout_seconds=60
        )
        values = json.loads(self._require_success(execution, f"inspection of container {reference}"))
        if not isinstance(values, list) or len(values) != 1:
            raise DockerError(f"Docker returned an unexpected container inspection for {reference}", execution)
        return values[0]

    def build(
        self,
        dockerfile: str,
        *,
        tag: str,
        platform: str,
        labels: Mapping[str, str],
        build_arguments: Mapping[str, str],
        evidence_directory: Path,
        timeout_seconds: float = 1800,
        context_directory: Path | None = None,
    ) -> ProcessExecution:
        temporary_context = None
        if context_directory is None:
            temporary_context = tempfile.TemporaryDirectory(prefix="brain-lab-build-")
            context = temporary_context.name
        else:
            context = str(context_directory.resolve())
        try:
            arguments = ["build", "--platform", platform, "--file", "-", "--tag", tag]
            for key, value in sorted(labels.items()):
                arguments.extend(["--label", f"{key}={value}"])
            for key, value in sorted(build_arguments.items()):
                arguments.extend(["--build-arg", f"{key}={value}"])
            arguments.append(context)
            execution = self._execute(
                arguments,
                evidence_directory=evidence_directory,
                timeout_seconds=timeout_seconds,
                stdin=dockerfile.encode("utf-8"),
            )
        finally:
            if temporary_context is not None:
                temporary_context.cleanup()
        self._require_success(execution, f"build of {tag}")
        return execution

    def import_bundle(
        self,
        root: Path,
        manifest: TreeManifest,
        *,
        destination_prefix: str,
        tag: str,
        platform: str,
        labels: Mapping[str, str],
        evidence_directory: Path,
        timeout_seconds: float = 1800,
    ) -> ProcessExecution:
        root = root.resolve()
        prefix = PurePosixPath(destination_prefix)
        if prefix.is_absolute() or ".." in prefix.parts:
            raise ValueError("bundle destination prefix must be a safe relative path")

        def write_tar(stream: BinaryIO) -> None:
            with tarfile.open(fileobj=stream, mode="w|", dereference=False) as archive:
                for entry in manifest.entries:
                    source = root / entry.path
                    arcname = str(prefix / entry.path)
                    info = archive.gettarinfo(str(source), arcname=arcname)
                    if entry.kind == "file":
                        with source.open("rb") as handle:
                            reader = _HashingReader(handle)
                            archive.addfile(info, reader)
                            if reader.total != entry.size or reader.digest.hexdigest() != entry.sha256:
                                raise RuntimeError(f"capture path changed while streaming: {entry.path}")
                    else:
                        archive.addfile(info)

        arguments = ["import", "--platform", platform]
        for key, value in sorted(labels.items()):
            arguments.extend(["--change", f"LABEL {key}={value}"])
        arguments.extend(["-", tag])
        execution = self._execute(
            arguments,
            evidence_directory=evidence_directory,
            timeout_seconds=timeout_seconds,
            stdin_writer=write_tar,
        )
        try:
            self._require_success(execution, f"import of {tag}")
        except DockerError as primary:
            try:
                cleanup = self._execute(
                    ["image", "rm", tag],
                    evidence_directory=evidence_directory / "cleanup",
                    timeout_seconds=60,
                )
                self._require_success(cleanup, f"cleanup of failed import {tag}")
            except DockerError as cleanup_error:
                raise DockerError(
                    f"{primary}; cleanup also failed, so image tag {tag} may survive",
                    primary.execution,
                    cleanup_error=str(cleanup_error),
                    survivor={"kind": "image", "id": tag},
                ) from primary
            raise
        return execution

    def start_container(
        self,
        image: str,
        *,
        name: str,
        platform: str,
        network: str,
        labels: Mapping[str, str],
        evidence_directory: Path,
        timeout_seconds: float = 120,
    ) -> tuple[str, ProcessExecution]:
        arguments = [
            "run",
            "--detach",
            "--name",
            name,
            "--platform",
            platform,
            "--network",
            network,
        ]
        for key, value in sorted(labels.items()):
            arguments.extend(["--label", f"{key}={value}"])
        arguments.extend([image, "tail", "-f", "/dev/null"])
        execution = self._execute(arguments, evidence_directory=evidence_directory, timeout_seconds=timeout_seconds)
        container_id = self._require_success(execution, f"start of {name}").strip()
        if not container_id:
            raise DockerError(f"Docker did not return a container ID for {name}", execution)
        return container_id, execution

    def create_container(
        self,
        image: str,
        *,
        name: str,
        platform: str,
        labels: Mapping[str, str],
        evidence_directory: Path,
        timeout_seconds: float = 120,
    ) -> tuple[str, ProcessExecution]:
        arguments = ["container", "create", "--name", name, "--platform", platform]
        for key, value in sorted(labels.items()):
            arguments.extend(["--label", f"{key}={value}"])
        arguments.extend([image, "/bin/true"])
        execution = self._execute(
            arguments,
            evidence_directory=evidence_directory,
            timeout_seconds=timeout_seconds,
        )
        container_id = self._require_success(execution, f"creation of {name}").strip()
        if not container_id:
            raise DockerError(f"Docker did not return a container ID for {name}", execution)
        return container_id, execution

    def exec(
        self,
        container: str,
        argv: Sequence[str],
        *,
        evidence_directory: Path,
        working_directory: str | None = None,
        environment: Mapping[str, str] | None = None,
        user: str | None = None,
        stdin: bytes | None = None,
        timeout_seconds: float = 300,
    ) -> ProcessExecution:
        arguments = ["exec"]
        if stdin is not None:
            arguments.append("--interactive")
        if working_directory:
            arguments.extend(["--workdir", working_directory])
        if user:
            arguments.extend(["--user", user])
        for key, value in sorted((environment or {}).items()):
            arguments.extend(["--env", f"{key}={value}"])
        arguments.extend([container, *argv])
        return self._execute(
            arguments,
            evidence_directory=evidence_directory,
            timeout_seconds=timeout_seconds,
            stdin=stdin,
        )

    def shell(self, container: str, *, shell: str = "/bin/bash") -> int:
        return self.runner.interactive([self.executable, "exec", "-it", container, shell])

    def copy_in(self, container: str, source: Path, destination: str, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(
            ["cp", str(source), f"{container}:{destination}"],
            evidence_directory=evidence_directory,
            timeout_seconds=600,
        )
        self._require_success(execution, "copy-in")
        return execution

    def copy_out(self, container: str, source: str, destination: Path, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(
            ["cp", f"{container}:{source}", str(destination)],
            evidence_directory=evidence_directory,
            timeout_seconds=600,
        )
        self._require_success(execution, "copy-out")
        return execution

    def stop(self, container: str, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(
            ["stop", "--time", "10", container], evidence_directory=evidence_directory, timeout_seconds=30
        )
        self._require_success(execution, f"stop of {container}")
        return execution

    def start_existing(self, container: str, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(
            ["start", container], evidence_directory=evidence_directory, timeout_seconds=60
        )
        self._require_success(execution, f"restart of {container}")
        return execution

    def rename_container(
        self, container: str, name: str, evidence_directory: Path
    ) -> ProcessExecution:
        execution = self._execute(
            ["container", "rename", container, name],
            evidence_directory=evidence_directory,
            timeout_seconds=60,
        )
        self._require_success(execution, f"rename of container {container}")
        return execution

    def remove_container(self, container: str, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(
            ["container", "rm", "--force", container], evidence_directory=evidence_directory, timeout_seconds=60
        )
        self._require_success(execution, f"removal of container {container}")
        return execution

    def commit(
        self,
        container: str,
        tag: str,
        labels: Mapping[str, str],
        evidence_directory: Path,
    ) -> tuple[str, ProcessExecution]:
        arguments = ["commit"]
        for key, value in sorted(labels.items()):
            arguments.extend(["--change", f"LABEL {key}={value}"])
        arguments.extend([container, tag])
        execution = self._execute(arguments, evidence_directory=evidence_directory, timeout_seconds=900)
        image_id = self._require_success(execution, f"commit of {container}").strip()
        return image_id, execution

    def tag(self, image: str, tag: str, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(["image", "tag", image, tag], evidence_directory=evidence_directory, timeout_seconds=60)
        self._require_success(execution, f"tagging of {image}")
        return execution

    def remove_image(self, image: str, evidence_directory: Path) -> ProcessExecution:
        execution = self._execute(["image", "rm", image], evidence_directory=evidence_directory, timeout_seconds=120)
        self._require_success(execution, f"removal of image {image}")
        return execution

    def list_managed(self, evidence_directory: Path) -> dict[str, list[dict[str, Any]]]:
        containers = self._execute(
            [
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                f"label={MANAGED_LABEL}=true",
                "--format",
                "{{json .}}",
            ],
            evidence_directory=evidence_directory / "containers",
            timeout_seconds=60,
        )
        images = self._execute(
            [
                "image",
                "ls",
                "--no-trunc",
                "--filter",
                f"label={MANAGED_LABEL}=true",
                "--format",
                "{{json .}}",
            ],
            evidence_directory=evidence_directory / "images",
            timeout_seconds=60,
        )
        container_lines = self._require_success(containers, "managed-container inventory").splitlines()
        image_lines = self._require_success(images, "managed-image inventory").splitlines()
        return {
            "containers": [json.loads(line) for line in container_lines if line],
            "images": [json.loads(line) for line in image_lines if line],
        }

    @staticmethod
    def verify_resource_labels(inspect: Mapping[str, Any], kind: str, resource_id: str) -> None:
        labels = inspect.get("Config", {}).get("Labels") or {}
        if labels.get(MANAGED_LABEL) != "true" or labels.get(KIND_LABEL) != kind or labels.get(ID_LABEL) != resource_id:
            raise DockerError(f"Docker resource labels do not match {kind} {resource_id}; refusing mutation")
