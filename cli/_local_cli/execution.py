"""Owner-preserving execution composition for the staged local CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Protocol
from _bootstrap.owner_attachment import OwnerAttachment

from _launcher.adapter import LauncherAdapter
from _launcher.context import LauncherContext

from .discovery import ComposedCommandEntry


@dataclass(frozen=True, slots=True)
class LocalExecutionProjection:
    owner: str
    command_id: str
    command_version: int
    structured_content: Mapping[str, object]
    json_text: str
    concise_text: str
    is_error: bool
    exit_code: int

    def __post_init__(self) -> None:
        if self.owner not in {"application", "launcher"}:
            raise ValueError("local execution projection requires an authoritative owner")
        if self.exit_code not in range(5):
            raise ValueError("local execution exit code must use categories 0-4")
        if not self.json_text or not self.concise_text:
            raise ValueError("structural local results require JSON and concise text")
        try:
            decoded = json.loads(self.json_text)
        except json.JSONDecodeError as exc:
            raise ValueError("local execution JSON must be valid") from exc
        if decoded != self.structured_content:
            raise ValueError("local execution JSON and structured result must agree")
        if self.is_error != (self.structured_content.get("status") != "ok"):
            raise ValueError("local execution error state must follow structural status")
        if self.exit_code != _envelope_exit_code(self.structured_content):
            raise ValueError("local execution exit category must follow structural result")


class OwnedCommandInvoker(Protocol):
    owner: str

    def invoke(
        self,
        entry: ComposedCommandEntry,
        payload: Mapping[str, object],
    ) -> LocalExecutionProjection: ...


@dataclass(frozen=True, slots=True)
class SelectedBrainProcess:
    vault_root: Path
    python: Path
    workspace: Path | None = None
    operator_key: str | None = None
    dry_run: bool = False

    def __post_init__(self) -> None:
        for name in ("vault_root", "python"):
            value = getattr(self, name)
            if not value.is_absolute():
                raise ValueError(f"selected Brain {name} must be absolute")
        if self.vault_root.is_symlink():
            raise ValueError("selected Brain root cannot be a symlink")
        if self.workspace is not None and not self.workspace.is_absolute():
            raise ValueError("selected Brain workspace must be absolute")
        if not isinstance(self.dry_run, bool):
            raise ValueError("selected Brain dry_run must be a boolean")

    @property
    def command_script(self) -> Path:
        return self.vault_root / ".brain-core" / "scripts" / "command.py"


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class ApplicationProcessInvoker:
    target: SelectedBrainProcess
    runner: ProcessRunner = subprocess.run
    owner_attachment: OwnerAttachment | None = None
    operation_id: str | None = None
    owner: str = field(default="application", init=False)

    def invoke(
        self,
        entry: ComposedCommandEntry,
        payload: Mapping[str, object],
    ) -> LocalExecutionProjection:
        _require_entry_owner(entry, self.owner)
        if not isinstance(payload, Mapping):
            raise ValueError("application command payload must be an object")
        if (
            self.target.command_script.is_symlink()
            or not self.target.command_script.is_file()
        ):
            raise RuntimeError("selected Brain command.py is missing or unsafe")
        noun, verb = entry.command_id.split(".", 1)
        argv = [
            str(self.target.python),
            str(self.target.command_script),
            noun,
            verb,
            "--request-json",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "--vault",
            str(self.target.vault_root),
            "--json",
        ]
        if self.target.workspace is not None:
            argv.extend(("--workspace", str(self.target.workspace)))
        if self.target.operator_key is not None:
            argv.extend(("--operator-key", self.target.operator_key))
        if self.target.dry_run:
            argv.append("--dry-run")
        if self.operation_id is not None:
            argv.extend(("--operation", self.operation_id))
        options = self.owner_attachment.forwarded_process() if self.owner_attachment is not None else {}
        completed = self.runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            **options,
        )
        if completed.returncode not in range(5):
            raise RuntimeError("selected Brain command returned an invalid exit category")
        stdout = completed.stdout
        if not stdout.strip():
            raise RuntimeError("selected Brain command returned no structural result")
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("selected Brain command returned invalid JSON") from exc
        _validate_child_envelope(entry, envelope, completed.returncode)
        return LocalExecutionProjection(
            self.owner,
            entry.command_id,
            entry.command_version,
            envelope,
            stdout.strip(),
            _concise_text(envelope),
            envelope["status"] != "ok",
            completed.returncode,
        )


@dataclass(frozen=True, slots=True)
class LauncherCommandInvoker:
    context: LauncherContext
    adapter: LauncherAdapter
    owner: str = field(default="launcher", init=False)

    def invoke(
        self,
        entry: ComposedCommandEntry,
        payload: Mapping[str, object],
    ) -> LocalExecutionProjection:
        _require_entry_owner(entry, self.owner)
        projection = self.adapter.invoke(self.context, entry.command_id, payload)
        if projection.result.command_version != entry.command_version:
            raise RuntimeError("launcher result version does not match composed discovery")
        return LocalExecutionProjection(
            self.owner,
            entry.command_id,
            entry.command_version,
            projection.structured_content,
            projection.json_text,
            projection.concise_text,
            projection.is_error,
            projection.exit_code,
        )


@dataclass(frozen=True, slots=True)
class LocalCliExecution:
    invokers: tuple[OwnedCommandInvoker, ...]

    def __post_init__(self) -> None:
        owners = tuple(invoker.owner for invoker in self.invokers)
        if owners != ("application", "launcher"):
            raise ValueError("local CLI execution requires application then launcher owners")

    def invoke(
        self,
        entry: ComposedCommandEntry,
        payload: Mapping[str, object],
    ) -> LocalExecutionProjection:
        invoker = next(item for item in self.invokers if item.owner == entry.owner)
        return invoker.invoke(entry, payload)


def render_local_result(
    projection: LocalExecutionProjection,
    *,
    json_mode: bool,
) -> tuple[str, str, int]:
    """Place one canonical local result on deterministic CLI streams."""

    if json_mode:
        return projection.json_text + "\n", "", projection.exit_code
    line = projection.concise_text + "\n"
    if projection.is_error:
        return "", line, projection.exit_code
    return line, "", projection.exit_code


def _require_entry_owner(entry: ComposedCommandEntry, owner: str) -> None:
    if entry.owner != owner:
        raise ValueError(f"{owner} invoker cannot execute {entry.owner} discovery")


def _validate_child_envelope(
    entry: ComposedCommandEntry,
    envelope,
    exit_code: int,
) -> None:
    validate_application_envelope(
        envelope, entry.command_id, exit_code, command_version=entry.command_version
    )


def validate_application_envelope(
    envelope,
    command_id: str,
    exit_code: int,
    *,
    command_version: int | None = None,
) -> None:
    """Validate selected-Brain identity and process status on both CLI routes."""
    if not isinstance(envelope, dict):
        raise RuntimeError("selected Brain command result must be a JSON object")
    if envelope.get("schema") != "brain.command-result/1":
        raise RuntimeError("selected Brain command result schema is unsupported")
    version = envelope.get("command_version")
    if (
        envelope.get("command") != command_id
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or (command_version is not None and version != command_version)
    ):
        raise RuntimeError("selected Brain command result identity changed after discovery")
    try:
        expected_exit = _envelope_exit_code(envelope)
    except ValueError as exc:
        raise RuntimeError(f"selected Brain {exc}") from exc
    if exit_code != expected_exit:
        raise RuntimeError("selected Brain result and exit category disagree")


def _envelope_exit_code(envelope: Mapping[str, object]) -> int:
    status = envelope.get("status")
    if status == "ok":
        return 0
    if status == "partial":
        return 1
    if status != "error":
        raise ValueError("command result status is invalid")
    error = envelope.get("error")
    if not isinstance(error, Mapping) or not isinstance(error.get("code"), str):
        raise ValueError("command error requires a structural error code")
    if error["code"] in {"invalid_request", "not_found", "conflict"}:
        return 2
    if error["code"] in {"authority_denied", "authorisation_required", "capability_unavailable"}:
        return 3
    return 4


def _concise_text(envelope: Mapping[str, object]) -> str:
    command_id = envelope["command"]
    status = envelope["status"]
    if status == "ok":
        return f"{command_id}: ok"
    error = envelope.get("error")
    if not isinstance(error, Mapping):
        raise RuntimeError("non-ok selected Brain result requires an error object")
    code = error.get("code")
    message = error.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        raise RuntimeError("selected Brain error requires code and message")
    if status == "partial":
        return f"{command_id}: partial — {message}"
    return f"{command_id}: {code} — {message}"
