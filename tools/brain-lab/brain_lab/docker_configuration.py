from __future__ import annotations

import json
import base64
import binascii
import os
import tempfile
from enum import StrEnum
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

from .process import CommandRunner, ProcessLaunchError
from .docker_errors import DockerError


class DockerConfigurationError(DockerError):
    def __init__(self, message: str):
        super().__init__(message, evidence_complete=False)


class DockerEndpointError(DockerConfigurationError):
    pass


class DockerDaemonMismatch(DockerConfigurationError):
    pass


class DockerCredentialIsolationError(DockerConfigurationError):
    pass


# An empty auth entry prevents Docker's automatic native credential-store discovery.
PUBLIC_CONFIG = {"auths": {"brain-lab.invalid": {}}}


class RegistryMode(StrEnum):
    PUBLIC = "public"
    INLINE_AUTH = "inline-auth"


class DockerConfiguration:
    def __init__(self, runner: CommandRunner, executable: str, credential_config: Path | None):
        self.runner = runner
        self.executable = executable
        self.credential_config = credential_config
        self.endpoint: str | None = None
        self.daemon_id: str | None = None
        self.discovery: dict = {}

    @property
    def registry_mode(self) -> RegistryMode:
        return RegistryMode.INLINE_AUTH if self.credential_config is not None else RegistryMode.PUBLIC

    def reset(self) -> None:
        self.endpoint = None
        self.daemon_id = None
        self.discovery = {}

    @staticmethod
    @contextmanager
    def _temporary_directory(prefix: str):
        try:
            with tempfile.TemporaryDirectory(prefix=prefix) as directory:
                yield directory
        except OSError as exc:
            raise DockerConfigurationError("Docker configuration or evidence filesystem operation failed") from exc

    def _probe(self, arguments: list[str], environment: Mapping[str, str]) -> str:
        with self._temporary_directory("brain-lab-discovery-") as directory:
            try:
                execution = self.runner.run(
                    [self.executable, *arguments], environment=environment,
                    replace_environment=True, evidence_directory=Path(directory), timeout_seconds=30,
                )
            except ProcessLaunchError as exc:
                raise DockerEndpointError("Docker invocation failed during endpoint discovery") from exc
            if not execution.succeeded or not execution.evidence_complete:
                raise DockerEndpointError("Docker endpoint discovery failed; no daemon fallback is permitted")
            # Docker warns and falls back when its configuration is malformed.
            if Path(execution.stderr.path).read_bytes():
                raise DockerEndpointError("Docker endpoint discovery emitted a diagnostic; resolve it before retrying")
            return Path(execution.stdout.path).read_text(encoding="utf-8").strip()

    def _resolve(self) -> None:
        ambient = dict(os.environ)
        if ambient.get("DOCKER_HOST") and ambient.get("DOCKER_CONTEXT"):
            raise DockerEndpointError("DOCKER_HOST and DOCKER_CONTEXT are both set; select one endpoint source")
        try:
            endpoint = json.loads(self._probe(
                ["context", "inspect", "--format", "{{json .}}"], ambient,
            ))
        except (ValueError, TypeError) as exc:
            raise DockerEndpointError("Docker context inspection returned invalid endpoint data") from exc
        if not isinstance(endpoint, dict) or not isinstance(endpoint.get("Name"), str) or not isinstance(endpoint.get("Endpoints"), dict):
            raise DockerEndpointError("Docker context inspection returned invalid context data")
        docker_endpoint = endpoint["Endpoints"].get("docker")
        if not isinstance(docker_endpoint, dict):
            raise DockerEndpointError("Docker context does not contain a Docker endpoint")
        host = docker_endpoint.get("Host")
        if not isinstance(host, str) or not host.startswith("unix:///"):
            raise DockerEndpointError("Brain Lab requires a resolved local Unix Docker socket; remote transports are unsupported")
        if any(ambient.get(key) for key in ("DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")):
            raise DockerEndpointError("TLS environment settings are ambiguous for a local Unix Docker socket")
        daemon_id = self._probe(["info", "--format", "{{.ID}}"], ambient)
        if not daemon_id or any(character.isspace() for character in daemon_id):
            raise DockerEndpointError("Docker did not return a valid daemon identity")
        try:
            versions = json.loads(self._probe(["version", "--format", "{{json .}}"], ambient))
            cli_version, server_version = versions["Client"]["Version"], versions["Server"]["Version"]
            if not all(isinstance(value, str) and value for value in (cli_version, server_version)):
                raise ValueError("missing version")
        except (ValueError, KeyError, TypeError) as exc:
            raise DockerEndpointError("Docker did not return CLI and server versions") from exc
        self.discovery = {
            "starting_context": endpoint.get("Name"),
            "starting_environment": {
                key: value if key in {"DOCKER_HOST", "DOCKER_CONTEXT"} else "<redacted>"
                for key, value in ambient.items()
                if key.startswith(("DOCKER_", "BUILDX_", "BUILDKIT_"))
            },
            "cli_version": cli_version, "server_version": server_version,
        }
        self.endpoint = host
        self.daemon_id = daemon_id

    def _credentials(self) -> dict:
        if self.credential_config is None:
            raise DockerCredentialIsolationError("Inline authentication requires an explicit credential configuration")
        try:
            value = json.loads(self.credential_config.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DockerCredentialIsolationError("Cannot read the explicit registry credential configuration") from exc
        if not isinstance(value, dict) or set(value) != {"auths"} or not isinstance(value["auths"], dict) or not value["auths"]:
            raise DockerCredentialIsolationError("Registry configuration must contain only non-empty inline auths; credential helpers are unsupported")
        for registry, credentials in value["auths"].items():
            if not registry or not isinstance(credentials, dict) or not credentials or set(credentials) - {"auth", "identitytoken", "registrytoken", "username", "password"}:
                raise DockerCredentialIsolationError("Registry configuration contains an unsupported credential entry")
            if not all(isinstance(item, str) for item in credentials.values()):
                raise DockerCredentialIsolationError("Registry credential values must be strings")
            keys = set(credentials)
            valid = False
            if keys == {"auth"}:
                try:
                    decoded = base64.b64decode(credentials["auth"], validate=True).decode("utf-8")
                    username, separator, secret = decoded.partition(":")
                    valid = bool(username and separator and secret)
                except (ValueError, UnicodeError, binascii.Error):
                    valid = False
            elif keys in ({"identitytoken"}, {"registrytoken"}, {"username", "password"}):
                valid = all(credentials.values())
            if not valid:
                raise DockerCredentialIsolationError("Registry credentials must select one complete non-empty inline authentication mode")
        return value

    @contextmanager
    def environment(self, evidence_directory: Path, *, mode: RegistryMode = RegistryMode.PUBLIC):
        if not isinstance(mode, RegistryMode):
            raise DockerCredentialIsolationError("Unknown registry security mode")
        credentials = self._credentials() if mode is RegistryMode.INLINE_AUTH else PUBLIC_CONFIG
        if self.endpoint is None:
            self._resolve()
        with self._temporary_directory("brain-lab-docker-") as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps(PUBLIC_CONFIG), encoding="utf-8")
            config.chmod(0o600)
            environment = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("DOCKER_", "BUILDX_", "BUILDKIT_"))
            }
            environment.update(DOCKER_CONFIG=directory, DOCKER_HOST=self.endpoint)
            evidence_directory.mkdir(parents=True, exist_ok=True)
            (evidence_directory / "docker-connection.json").write_text(json.dumps({
                **self.discovery,
                "endpoint": self.endpoint, "daemon_id": self.daemon_id,
                "credential_mode": mode.value,
                "credential_helpers": "disabled", "endpoint_pinned": True,
            }, indent=2) + "\n", encoding="utf-8")
            actual = self._probe(["info", "--format", "{{.ID}}"], environment)
            if actual != self.daemon_id:
                raise DockerDaemonMismatch("Docker daemon identity changed after endpoint pinning; refusing the operation")
            config.write_text(json.dumps(credentials), encoding="utf-8")
            yield environment
