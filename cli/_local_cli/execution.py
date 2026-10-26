"""Owner-preserving execution composition for the staged local CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Protocol

from _bootstrap.process_text import machine_process_options
from _bootstrap.owner_attachment import OwnerAttachment
from _common._operational_log import matching_command_failure
from _common._venv import ROLE_CLI, run_managed

from _launcher.adapter import LauncherAdapter
from _launcher.context import LauncherContext

from .discovery import ComposedCommandEntry
from .parser import LocalCliUsageError


def machine_json_text(text: str) -> str:
    """Keep public JSON usable through legacy Windows codepage pipes."""
    return text if text.isascii() else json.dumps(json.loads(text), ensure_ascii=True, separators=(",", ":"))


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
    diagnostic_text: str | None = None

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
        source_navigation = _is_retained_source(self.command_id, self.structured_content)
        if self.is_error != (not source_navigation and self.structured_content.get("status") != "ok"):
            raise ValueError("local execution error state must follow structural status")
        source_exit = _envelope_exit_code(self.structured_content)
        if self.exit_code != (0 if source_navigation else source_exit):
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


def reject_invalid_result_session(completed: subprocess.CompletedProcess[str]) -> None:
    """Preserve the released Core's owned pre-invocation refusal as CLI usage."""
    evidence = (
        "command.py: invalid request — invalid result session; select its private file "
        "or run session start --result-session-file <new-private-path>"
    )
    if (completed.returncode == 2 and completed.stdout == ""
            and completed.stderr == evidence + "\n"):
        raise LocalCliUsageError(
            "Invalid result session. Select a valid private result-session file for this Brain. "
            "In a managed CLI job, omit the child's --result-session-file to inherit the job's session, "
            "or pass the same --result-session-file to both the outer brain session run and its child."
        )


def application_process_diagnostic(exit_code: int, stderr: str) -> str:
    """Classify bounded owned evidence without echoing arbitrary child streams."""
    diagnostic = f" (exit {exit_code})"
    # Only recognise owned evidence; child streams may contain credentials.
    evidence = stderr[-4096:].strip()
    if evidence == "command.py: infrastructure — direct workspace is not bound to the selected Brain":
        diagnostic += (
            "; the command requires a workspace already bound to the selected Brain. "
            "Register the intended local folder with brain workspace register --brain <registered-brain-id> "
            "--workspace <local-folder> --request-json '{\"slug\":\"<workspace-key>\"}'. "
            "If the workspace hub does not exist, first run brain workspace create "
            "--brain <registered-brain-id> "
            "--request-json '{\"key\":\"<workspace-key>\",\"title\":\"<workspace-title>\"}', then register it."
        )
    elif evidence.startswith("command.py: infrastructure — Workspace metadata schema ") and " Run brain workspace upgrade" in evidence:
        diagnostic += (
            "; Workspace metadata needs upgrading. Review and run brain workspace upgrade "
            "--brain <registered-brain-id> --workspace <local-folder>."
        )
    elif evidence.startswith("command.py: infrastructure — direct workspace cannot be resolved:"):
        diagnostic += (
            "; the child could not resolve the workspace binding. "
            "Check the workspace manifest and local Brain registry."
        )
    else:
        diagnostic += "; check the selected Brain command entry point and runtime"
    return diagnostic


@dataclass(frozen=True, slots=True)
class ApplicationProcessInvoker:
    target: SelectedBrainProcess
    runner: ProcessRunner = run_managed
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
        options = machine_process_options(self.owner_attachment.forwarded_process() if self.owner_attachment is not None else {})
        completed = self.runner(
            argv,
            role=ROLE_CLI,
            capture_output=True,
            check=False,
            **options,
        )
        reject_invalid_result_session(completed)
        diagnostic = application_process_diagnostic(completed.returncode, completed.stderr)
        if completed.returncode not in range(5):
            raise RuntimeError("selected Brain command returned an invalid exit category" + diagnostic)
        stdout = completed.stdout
        if not stdout.strip():
            raise RuntimeError("selected Brain command returned no structural result" + diagnostic)
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("selected Brain command returned invalid JSON" + diagnostic) from exc
        _validate_child_envelope(entry, envelope, completed.returncode, result_id=payload.get("result_id"))
        diagnostic = None
        error = envelope.get("error", {})
        details = error.get("details") if isinstance(error, Mapping) else None
        if (envelope["status"] == "error" and error.get("code") == "internal_error"
                and isinstance(details, Mapping) and isinstance(details.get("correlation_id"), str)):
            diagnostic = matching_command_failure(
                completed.stderr, process="script", command_id=entry.command_id,
                correlation_id=details["correlation_id"],
            )
        return LocalExecutionProjection(
            self.owner,
            entry.command_id,
            entry.command_version,
            envelope,
            stdout.strip(),
            _concise_text(envelope),
            not _is_retained_source(entry.command_id, envelope) and envelope["status"] != "ok",
            completed.returncode,
            diagnostic,
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

    diagnostic = projection.diagnostic_text + "\n" if projection.diagnostic_text else ""
    if json_mode:
        return machine_json_text(projection.json_text) + "\n", diagnostic, projection.exit_code
    line = projection.concise_text + "\n"
    if projection.is_error:
        return "", line + diagnostic, projection.exit_code
    return line, diagnostic, projection.exit_code


def _require_entry_owner(entry: ComposedCommandEntry, owner: str) -> None:
    if entry.owner != owner:
        raise ValueError(f"{owner} invoker cannot execute {entry.owner} discovery")


def _validate_child_envelope(
    entry: ComposedCommandEntry,
    envelope,
    exit_code: int,
    *,
    result_id: object = None,
) -> None:
    validate_application_envelope(
        envelope, entry.command_id, exit_code, command_version=entry.command_version, result_id=result_id
    )


def validate_application_envelope(
    envelope,
    command_id: str,
    exit_code: int,
    *,
    command_version: int | None = None,
    result_id: object = None,
) -> None:
    """Validate selected-Brain identity and process status on both CLI routes."""
    if not isinstance(envelope, dict):
        raise RuntimeError("selected Brain command result must be a JSON object")
    if envelope.get("schema") != "brain.command-result/1":
        raise RuntimeError("selected Brain command result schema is unsupported")
    source_navigation = _is_retained_source(command_id, envelope)
    if source_navigation:
        delivery = envelope.get("delivery")
        if (not isinstance(result_id, str) or not result_id
                or not isinstance(delivery, dict) or delivery.get("result_id") != result_id
                or not isinstance(envelope.get("command"), str) or not envelope["command"]):
            raise RuntimeError("selected Brain retained result identity changed after discovery")
        command_id = envelope["command"]
        command_version = None
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
    if exit_code != (0 if source_navigation else expected_exit):
        raise RuntimeError("selected Brain result and exit category disagree")


def _is_retained_source(command_id: str, envelope: Mapping[str, object]) -> bool:
    return command_id == "result.read" and envelope.get("command") != command_id


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
