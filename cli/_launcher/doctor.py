"""Typed effect-free owner for machine and optional Brain diagnosis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from .context import LauncherContext
from .contracts import Ok, validate_command_id


class DoctorSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DoctorRegistryState(str, Enum):
    CURRENT = "current"
    DRIFTED = "drifted"
    MALFORMED = "malformed"
    BLOCKED = "blocked"


class DoctorVaultState(str, Enum):
    NOT_SCOPED = "not_scoped"
    UNAVAILABLE = "unavailable"
    CHECKED = "checked"


def _validate_absolute(value: str, field: str) -> None:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"{field} must be an absolute path")


def _validate_optional_text(value: str | None, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{field} must be non-empty when present")


@dataclass(frozen=True, slots=True)
class DoctorCliStatus:
    version: str
    binary: str
    binary_dir: str
    path_ok: bool
    launcher_python: str | None
    launcher_version: str | None
    launcher_probe_failed: bool

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Doctor CLI version must be non-empty")
        _validate_absolute(self.binary, "Doctor CLI binary")
        _validate_absolute(self.binary_dir, "Doctor CLI binary directory")
        if self.launcher_python is not None:
            _validate_absolute(self.launcher_python, "Doctor launcher Python")
        _validate_optional_text(self.launcher_version, "Doctor launcher version")
        if not isinstance(self.path_ok, bool) or not isinstance(
            self.launcher_probe_failed,
            bool,
        ):
            raise ValueError("Doctor CLI health flags must be boolean")


@dataclass(frozen=True, slots=True)
class DoctorRegistryStatus:
    state: DoctorRegistryState
    path: str
    brains_count: int
    blocked_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, DoctorRegistryState):
            raise ValueError("Doctor registry state must be closed and typed")
        _validate_absolute(self.path, "Doctor machine registry")
        if self.brains_count < 0:
            raise ValueError("Doctor registry Brain count cannot be negative")
        _validate_optional_text(self.blocked_reason, "Doctor registry block reason")


@dataclass(frozen=True, slots=True)
class DoctorPathEntry:
    alias: str | None
    path: str

    def __post_init__(self) -> None:
        _validate_optional_text(self.alias, "Doctor path alias")
        _validate_absolute(self.path, "Doctor path entry")


@dataclass(frozen=True, slots=True)
class DoctorRepairFinding:
    scope: str
    message: str
    command_id: str | None

    def __post_init__(self) -> None:
        if not self.scope.strip() or not self.message.strip():
            raise ValueError("Doctor repair findings require scope and message")
        if self.command_id is not None:
            validate_command_id(self.command_id)


@dataclass(frozen=True, slots=True)
class DoctorBrainStatus:
    alias: str | None
    vault_root: str
    sources: tuple[str, ...]
    runtime_status: str
    runtime_message: str
    selected_runtime: str | None
    expected_runtime: str
    legacy_runtime_present: bool
    repair_findings: tuple[DoctorRepairFinding, ...]

    def __post_init__(self) -> None:
        _validate_optional_text(self.alias, "Doctor Brain alias")
        _validate_absolute(self.vault_root, "Doctor Brain root")
        if not self.sources or any(not value.strip() for value in self.sources):
            raise ValueError("Doctor Brain sources must be non-empty")
        if len(self.sources) != len(set(self.sources)):
            raise ValueError("Doctor Brain sources must be unique")
        if not self.runtime_status.strip() or not self.runtime_message.strip():
            raise ValueError("Doctor Brain runtime status must be complete")
        if self.selected_runtime is not None:
            _validate_absolute(self.selected_runtime, "Doctor selected runtime")
        _validate_absolute(self.expected_runtime, "Doctor expected runtime")
        if not isinstance(self.legacy_runtime_present, bool):
            raise ValueError("Doctor legacy-runtime state must be boolean")
        if any(
            not isinstance(finding, DoctorRepairFinding)
            for finding in self.repair_findings
        ):
            raise ValueError("Doctor Brain repair findings must be typed")


@dataclass(frozen=True, slots=True)
class DoctorMachineCounts:
    brains: int
    repair_findings: int
    stale_vault_registry_entries: int
    stale_machine_registry_entries: int
    runtimes: int
    orphan_candidates: int

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.brains,
                self.repair_findings,
                self.stale_vault_registry_entries,
                self.stale_machine_registry_entries,
                self.runtimes,
                self.orphan_candidates,
            )
        ):
            raise ValueError("Doctor machine counts cannot be negative")


@dataclass(frozen=True, slots=True)
class DoctorHeavyProcess:
    pid: int
    footprint_bytes: int
    command: str

    def __post_init__(self) -> None:
        if self.pid <= 0 or self.footprint_bytes < 0 or not self.command:
            raise ValueError("Doctor heavy process must have a pid, footprint and command")


@dataclass(frozen=True, slots=True)
class DoctorMemoryStatus:
    """Advisory physical footprint of live Brain runtime processes."""

    process_count: int
    measured_count: int
    total_bytes: int
    process_warn_bytes: int
    total_warn_bytes: int
    total_over_threshold: bool
    heavy_processes: tuple[DoctorHeavyProcess, ...]

    def __post_init__(self) -> None:
        if min(self.process_count, self.measured_count, self.total_bytes) < 0:
            raise ValueError("Doctor memory counts cannot be negative")
        if self.measured_count > self.process_count:
            raise ValueError("Doctor memory cannot measure more processes than are live")
        if any(not isinstance(item, DoctorHeavyProcess) for item in self.heavy_processes):
            raise ValueError("Doctor heavy processes must be typed")
        if any(item.footprint_bytes <= self.process_warn_bytes for item in self.heavy_processes):
            raise ValueError("Doctor heavy processes must exceed the per-process threshold")
        if self.total_over_threshold != (self.total_bytes > self.total_warn_bytes):
            raise ValueError("Doctor memory total flag must agree with the threshold")


@dataclass(frozen=True, slots=True)
class DoctorMachineStatus:
    healthy: bool
    tidy: bool
    live_process_scan_available: bool
    venvs_root: str
    registry: DoctorRegistryStatus
    counts: DoctorMachineCounts
    stale_vault_registry_entries: tuple[DoctorPathEntry, ...]
    stale_machine_registry_entries: tuple[DoctorPathEntry, ...]
    brains: tuple[DoctorBrainStatus, ...]
    orphan_runtime_pythons: tuple[str, ...]
    memory: DoctorMemoryStatus | None

    def __post_init__(self) -> None:
        if self.memory is not None and not isinstance(self.memory, DoctorMemoryStatus):
            raise ValueError("Doctor machine memory must be typed")
        if any(
            not isinstance(value, bool)
            for value in (
                self.healthy,
                self.tidy,
                self.live_process_scan_available,
            )
        ):
            raise ValueError("Doctor machine health flags must be boolean")
        _validate_absolute(self.venvs_root, "Doctor central runtime root")
        if not isinstance(self.registry, DoctorRegistryStatus) or not isinstance(
            self.counts,
            DoctorMachineCounts,
        ):
            raise ValueError("Doctor machine registry and counts must be typed")
        typed_collections = (
            (self.stale_vault_registry_entries, DoctorPathEntry),
            (self.stale_machine_registry_entries, DoctorPathEntry),
            (self.brains, DoctorBrainStatus),
        )
        if any(
            not isinstance(item, expected)
            for values, expected in typed_collections
            for item in values
        ):
            raise ValueError("Doctor machine collections must be typed")
        for path in self.orphan_runtime_pythons:
            _validate_absolute(path, "Doctor orphan runtime")
        if self.orphan_runtime_pythons != tuple(sorted(set(self.orphan_runtime_pythons))):
            raise ValueError("Doctor orphan runtimes must be sorted and unique")
        if (
            self.counts.brains != len(self.brains)
            or self.counts.stale_vault_registry_entries
            != len(self.stale_vault_registry_entries)
            or self.counts.stale_machine_registry_entries
            != len(self.stale_machine_registry_entries)
            or self.counts.orphan_candidates != len(self.orphan_runtime_pythons)
        ):
            raise ValueError("Doctor machine counts must agree with projected rows")


@dataclass(frozen=True, slots=True)
class DoctorVaultSummary:
    errors: int
    warnings: int
    info: int

    def __post_init__(self) -> None:
        if min(self.errors, self.warnings, self.info) < 0:
            raise ValueError("Doctor vault counts cannot be negative")


@dataclass(frozen=True, slots=True)
class DoctorVaultFinding:
    check: str
    severity: DoctorSeverity
    file: str | None
    message: str
    repair_command_id: str | None

    def __post_init__(self) -> None:
        if not self.check.strip() or not self.message.strip():
            raise ValueError("Doctor vault findings require check and message")
        _validate_optional_text(self.file, "Doctor finding file")
        if not isinstance(self.severity, DoctorSeverity):
            raise ValueError("Doctor finding severity must be closed and typed")
        if self.repair_command_id is not None:
            validate_command_id(self.repair_command_id)


@dataclass(frozen=True, slots=True)
class DoctorVaultStatus:
    state: DoctorVaultState
    vault_root: str | None
    exit_code: int
    message: str | None
    summary: DoctorVaultSummary | None
    findings: tuple[DoctorVaultFinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, DoctorVaultState) or self.exit_code < 0:
            raise ValueError("Doctor vault state and exit code must be valid")
        if self.vault_root is not None:
            _validate_absolute(self.vault_root, "Doctor vault root")
        _validate_optional_text(self.message, "Doctor vault message")
        if self.state is DoctorVaultState.NOT_SCOPED:
            if self.vault_root is not None or self.summary is not None or self.findings:
                raise ValueError("unscoped Doctor vault cannot carry vault results")
        elif self.state is DoctorVaultState.UNAVAILABLE:
            if self.vault_root is None or self.message is None or self.summary is not None:
                raise ValueError("unavailable Doctor vault requires root and message")
        elif self.vault_root is None or self.summary is None or self.message is not None:
            raise ValueError("checked Doctor vault requires root and summary")
        if any(not isinstance(finding, DoctorVaultFinding) for finding in self.findings):
            raise ValueError("Doctor vault findings must be typed")


@dataclass(frozen=True, slots=True)
class BrainDoctorPayload:
    healthy: bool
    exit_code: int
    cli: DoctorCliStatus
    machine: DoctorMachineStatus
    vault: DoctorVaultStatus

    def __post_init__(self) -> None:
        if not isinstance(self.healthy, bool) or self.exit_code < 0:
            raise ValueError("brain.doctor health and exit code must be valid")
        if self.healthy != (self.exit_code == 0):
            raise ValueError("brain.doctor health must agree with exit code")
        if not isinstance(self.cli, DoctorCliStatus) or not isinstance(
            self.machine,
            DoctorMachineStatus,
        ) or not isinstance(self.vault, DoctorVaultStatus):
            raise ValueError("brain.doctor nested results must be typed")


@dataclass(frozen=True, slots=True)
class BrainDoctorRequest:
    COMMAND_ID: ClassVar[str] = "brain.doctor"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainDoctorPayload

    current_vault: Path | None = None
    actionable: bool = False
    severity: DoctorSeverity | None = None

    def __post_init__(self) -> None:
        if self.current_vault is not None and not self.current_vault.is_absolute():
            raise ValueError("brain.doctor current_vault must be absolute")
        if not isinstance(self.actionable, bool):
            raise ValueError("brain.doctor actionable must be boolean")
        if self.severity is not None and not isinstance(self.severity, DoctorSeverity):
            raise ValueError("brain.doctor severity must be closed and typed")


_REPAIR_COMMANDS = {
    "frontmatter": "artefact.repair",
    "lexical": "retrieval.refresh-lexical",
    "mcp": "mcp.repair",
    "ownership": "artefact.repair",
    "registry": "workspace.repair-registry",
    "router": "runtime.refresh-router",
    "runtime": "runtime.repair",
    "semantic": "retrieval.repair-semantic",
}


def _repair_finding(raw: dict) -> DoctorRepairFinding:
    repair = raw["repair"]
    scope = repair["scope"]
    return DoctorRepairFinding(scope, raw["message"], _REPAIR_COMMANDS.get(scope))


def _registry_status(raw: dict) -> DoctorRegistryStatus:
    if raw["blocked"]:
        state = DoctorRegistryState.BLOCKED
    elif raw.get("malformed", False):
        state = DoctorRegistryState.MALFORMED
    elif raw.get("drifted", False):
        state = DoctorRegistryState.DRIFTED
    else:
        state = DoctorRegistryState.CURRENT
    return DoctorRegistryStatus(
        state,
        raw["path"],
        raw["brains_count"],
        raw.get("blocked_reason"),
    )


def _machine_status(raw: dict) -> DoctorMachineStatus:
    counts = raw["counts"]
    brains = tuple(
        DoctorBrainStatus(
            item.get("alias"),
            item["path"],
            tuple(item["sources"]),
            item["runtime"]["status"],
            item["runtime"]["message"],
            item["runtime"]["selected_runtime"],
            item["runtime"]["expected_runtime"],
            item["runtime"]["legacy_runtime_present"],
            tuple(_repair_finding(finding) for finding in item["repair_findings"]),
        )
        for item in raw["brains"]
    )
    return DoctorMachineStatus(
        raw["healthy"],
        raw["tidy"],
        raw["live_process_scan_available"],
        raw["venvs_root"],
        _registry_status(raw["machine_registry"]),
        DoctorMachineCounts(
            counts["brains"],
            counts["repair_findings"],
            counts["stale_registry_entries"],
            counts["stale_machine_registry_entries"],
            counts["runtimes"],
            counts["orphan_candidates"],
        ),
        tuple(
            DoctorPathEntry(item["alias"], item["path"])
            for item in raw["stale_registry_entries"]
        ),
        tuple(
            DoctorPathEntry(item.get("alias"), item["path"])
            for item in raw["stale_machine_registry_entries"]
        ),
        brains,
        tuple(
            sorted(
                item["python"]
                for item in raw["runtimes"]
                if item["orphan_candidate"]
            )
        ),
        _memory_status(raw.get("memory")),
    )


def _memory_status(raw: dict | None) -> DoctorMemoryStatus | None:
    if raw is None:
        return None
    return DoctorMemoryStatus(
        raw["process_count"],
        raw["measured_count"],
        raw["total_bytes"],
        raw["process_warn_bytes"],
        raw["total_warn_bytes"],
        raw["total_over_threshold"],
        tuple(
            DoctorHeavyProcess(item["pid"], item["footprint_bytes"], item["command"])
            for item in raw["heavy_processes"]
        ),
    )


def _vault_finding(raw: dict) -> DoctorVaultFinding:
    repair = raw.get("repair")
    scope = repair.get("scope") if isinstance(repair, dict) else None
    return DoctorVaultFinding(
        raw.get("check") or "unspecified",
        DoctorSeverity(raw["severity"]),
        raw["file"],
        raw["message"],
        _REPAIR_COMMANDS.get(scope),
    )


def _vault_status(raw: dict) -> DoctorVaultStatus:
    if not raw["in_scope"]:
        return DoctorVaultStatus(
            DoctorVaultState.NOT_SCOPED,
            None,
            raw["exit_code"],
            raw["message"],
            None,
            (),
        )
    if not raw["available"]:
        return DoctorVaultStatus(
            DoctorVaultState.UNAVAILABLE,
            raw["vault_root"],
            raw["exit_code"],
            raw["message"],
            None,
            (),
        )
    result = raw["result"]
    summary = result["summary"]
    return DoctorVaultStatus(
        DoctorVaultState.CHECKED,
        raw["vault_root"],
        raw["exit_code"],
        None,
        DoctorVaultSummary(summary["errors"], summary["warnings"], summary["info"]),
        tuple(_vault_finding(finding) for finding in result["findings"]),
    )


def execute_doctor(context: LauncherContext, request: BrainDoctorRequest):
    import doctor

    launcher_python = (
        str(context.launcher_python) if context.launcher_python is not None else None
    )
    current_vault = (
        str(request.current_vault) if request.current_vault is not None else None
    )
    cli = doctor.collect_cli_diagnosis(
        binary_path=str(context.cli_binary),
        cli_version=context.cli_version,
        launcher_python=launcher_python,
    )
    machine = doctor.doctor_machine.collect_machine_summary(
        current_vault=current_vault,
        launcher_python=launcher_python,
        synchronise_registry=False,
        measure_memory=True,
    )
    vault = doctor.collect_vault_diagnosis(
        current_vault=current_vault,
        launcher_python=launcher_python,
        actionable=request.actionable,
        severity=request.severity.value if request.severity is not None else None,
    )
    exit_code = doctor.overall_exit_code(cli=cli, machine=machine, vault=vault)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainDoctorPayload(
            exit_code == 0,
            exit_code,
            DoctorCliStatus(
                cli["version"],
                cli["binary"],
                cli["binary_dir"],
                cli["path_ok"],
                cli["launcher_python"],
                cli["launcher_version"],
                cli["launcher_probe_failed"],
            ),
            _machine_status(machine),
            _vault_status(vault),
        ),
    )


def doctor_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainDoctorRequest,
        BrainDoctorPayload,
        "_launcher.doctor:doctor",
        execute_doctor,
    )
