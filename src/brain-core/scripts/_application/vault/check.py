"""Typed ``vault.check`` command and bounded diagnostic projection."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


class CheckSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_REPAIR_COMMANDS = {
    "empty_folders": "artefact.repair",
    "frontmatter": "artefact.repair",
    "lexical": "retrieval.refresh-lexical",
    "mcp": "mcp.repair",
    "ownership": "artefact.repair",
    "registry": "workspace.repair-registry",
    "router": "runtime.refresh-router",
    "runtime": "runtime.repair",
    "semantic": "retrieval.repair-semantic",
}


@dataclass(frozen=True, slots=True)
class CheckRepairAction:
    scope: str
    description: str
    command_id: str


@dataclass(frozen=True, slots=True)
class VaultCheckFinding:
    check: str
    severity: CheckSeverity
    file: str | None
    message: str
    fix: str | None
    repair: CheckRepairAction | None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class VaultCheckPayload:
    findings: tuple[VaultCheckFinding, ...]
    errors: int
    warnings: int
    info: int


@dataclass(frozen=True, slots=True)
class VaultCheckRequest:
    COMMAND_ID: ClassVar[str] = "vault.check"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = VaultCheckPayload

    severity: CheckSeverity | None = None
    check: str | None = None
    path: str | None = None
    actionable: bool = False

    def __post_init__(self) -> None:
        if self.severity is not None and not isinstance(self.severity, CheckSeverity):
            raise ValueError("vault.check severity must be a CheckSeverity")
        for name in ("check", "path"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"vault.check {name} must be a non-empty string")
        if not isinstance(self.actionable, bool):
            raise ValueError("vault.check actionable must be a boolean")


def execute(context: InvocationContext, request: VaultCheckRequest):
    import check

    try:
        result = check.run_checks(context.selected_brain.vault_root, workspace_dir=context.workspace_dir)
        result = check.filter_and_summarize_findings(
            result,
            severity=request.severity.value if request.severity else None,
            check_name=request.check,
            path=request.path,
        )
    except (OSError, ValueError) as exc:
        return command_error(VaultCheckRequest, ErrorCode.CONFLICT, str(exc), None)

    findings = []
    for source in result["findings"]:
        repair_source = source.get("repair")
        repair = None
        if repair_source:
            scope = repair_source["scope"]
            command_id = _REPAIR_COMMANDS.get(scope)
            if command_id is None:
                return command_error(
                    VaultCheckRequest,
                    ErrorCode.INTERNAL_ERROR,
                    f"Unsupported repair scope: {scope}",
                    None,
                )
            repair = CheckRepairAction(
                scope,
                repair_source["description"],
                command_id,
            )
        findings.append(
            VaultCheckFinding(
                check=source["check"],
                severity=CheckSeverity(source["severity"]),
                file=source.get("file"),
                message=source["message"],
                fix=source.get("fix") if request.actionable and repair is None else None,
                repair=repair,
                code=source.get("code"),
            )
        )
    summary = result["summary"]
    return Ok(
        VaultCheckRequest.COMMAND_ID,
        VaultCheckRequest.COMMAND_VERSION,
        VaultCheckPayload(
            tuple(findings),
            summary["errors"],
            summary["warnings"],
            summary["info"],
        ),
    )


def decode(payload: Mapping[str, object]) -> VaultCheckRequest:
    allowed = {"severity", "check", "path", "actionable"}
    reject_unexpected(payload, allowed)
    severity = payload.get("severity")
    check_name = payload.get("check")
    path = payload.get("path")
    actionable = payload.get("actionable", False)
    if severity is not None and not isinstance(severity, str):
        raise ValueError("severity must be a string")
    if check_name is not None and not isinstance(check_name, str):
        raise ValueError("check must be a string")
    if path is not None and not isinstance(path, str):
        raise ValueError("path must be a string")
    if not isinstance(actionable, bool):
        raise ValueError("actionable must be a boolean")
    return VaultCheckRequest(
        severity=CheckSeverity(severity) if severity is not None else None,
        check=check_name,
        path=path,
        actionable=actionable,
    )


def catalogue_entry():
    return _catalogue_entry(VaultCheckRequest, execute)
