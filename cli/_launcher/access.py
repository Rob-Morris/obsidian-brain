"""CLI-only external access approval owner."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import ClassVar, Literal

from .context import LauncherContext
from .contracts import (
    CommandError,
    CommittedEffect,
    Error,
    ErrorCode,
    Ok,
)


@dataclass(frozen=True, slots=True)
class AccessApprovePayload:
    status: Literal["planned", "approved"]
    principal: str
    request_id: str
    commands: tuple[str, ...]
    lease_id: str | None
    expires_at: str | None

    def __post_init__(self) -> None:
        if self.status not in {"planned", "approved"}:
            raise ValueError("access approval status is invalid")
        if not self.principal.strip() or not self.request_id.strip():
            raise ValueError("access approval identity is invalid")
        if not self.commands or self.commands != tuple(sorted(set(self.commands))):
            raise ValueError("access approval commands must be sorted and unique")
        if self.status == "approved" and (
            self.lease_id is None or self.expires_at is None
        ):
            raise ValueError("approved access requires lease identity and expiry")
        if self.status == "planned" and (
            self.lease_id is not None or self.expires_at is not None
        ):
            raise ValueError("planned access cannot claim an issued lease")


@dataclass(frozen=True, slots=True)
class AccessApproveRequest:
    COMMAND_ID: ClassVar[str] = "access.approve"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = AccessApprovePayload

    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("access approval requires a request_id")


def execute(context: LauncherContext, request: AccessApproveRequest):
    if context.current_vault is None:
        return _denied(request, "No current Brain is selected for access approval.")
    if not context.operator_key:
        return _denied(
            request,
            "External access approval requires a registered operator key.",
        )
    helper = context.current_vault / ".brain-core/scripts/access_approval.py"
    if helper.is_symlink() or not helper.is_file():
        return _denied(
            request,
            "The selected Brain does not provide the external approval helper.",
        )
    python = context.launcher_python
    if python is None or python.is_symlink() or not python.is_file():
        raise RuntimeError("launcher Python is unavailable for access approval")
    environment = os.environ.copy()
    environment["BRAIN_ACCESS_APPROVER_KEY"] = context.operator_key
    argv = [
        str(python),
        str(helper),
        "--vault",
        str(context.current_vault),
        "--request-id",
        request.request_id,
    ]
    if context.dry_run:
        argv.append("--dry-run")
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if completed.returncode == 2:
        message = completed.stderr.strip() or "External access approval was denied."
        return _denied(request, message)
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError("external approval helper failed its process contract")
    try:
        value = json.loads(completed.stdout)
        payload = _payload(value)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("external approval helper returned invalid output") from exc
    effects = (
        (CommittedEffect("access.approved", f"{payload.principal}:{payload.lease_id}"),)
        if payload.status == "approved"
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        effects,
    )


def _payload(value: object) -> AccessApprovePayload:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "principal",
        "request_id",
        "commands",
        "lease",
    }:
        raise ValueError("access approval payload has an invalid shape")
    commands = value["commands"]
    if not isinstance(commands, list) or any(not isinstance(item, str) for item in commands):
        raise ValueError("access approval commands are invalid")
    lease = value["lease"]
    if lease is None:
        lease_id = None
        expires_at = None
    elif isinstance(lease, dict):
        lease_id = lease.get("lease_id")
        expires_at = lease.get("expires_at")
        if not isinstance(lease_id, str) or not isinstance(expires_at, str):
            raise ValueError("access approval lease is invalid")
    else:
        raise ValueError("access approval lease is invalid")
    status = value["status"]
    principal = value["principal"]
    request_id = value["request_id"]
    if status not in {"planned", "approved"}:
        raise ValueError("access approval status is invalid")
    if not isinstance(principal, str) or not isinstance(request_id, str):
        raise ValueError("access approval identity is invalid")
    return AccessApprovePayload(
        status,
        principal,
        request_id,
        tuple(commands),
        lease_id,
        expires_at,
    )


def _denied(request: AccessApproveRequest, message: str):
    return Error(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        CommandError(ErrorCode.AUTHORITY_DENIED, message),
    )


def approve_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        AccessApproveRequest,
        AccessApprovePayload,
        "_launcher.access:approve",
        execute,
    )
