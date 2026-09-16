"""Concrete machine-local composition for the Brain CLI application."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import OutcomeReceipt, ReceiptState
from _distribution import source_versions


CLI_VERSION = source_versions(Path(__file__).resolve().parents[2]).cli_version
CUTOVER_BRAIN_VERSION = (0, 55, 0)

# Mirrors the vault receipt store's ReceiptPolicy bounds.
_RECEIPT_RETENTION_SECONDS = 7 * 24 * 3600
_RECEIPT_MAX_RECORDS = 10_000


@dataclass(frozen=True, slots=True)
class LocalAuthority:
    def allows(self, *, command_id: str, required: str, effect: str) -> bool:
        del command_id, required, effect
        return True


@dataclass(frozen=True, slots=True)
class LocalProvider:
    provider_id: str
    available: bool = True


@dataclass(frozen=True, slots=True)
class LocalClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SelectedBrain:
    vault_root: Path
    workspace: Path | None
    source: str

    @property
    def command_script(self) -> Path:
        return self.vault_root / ".brain-core" / "scripts" / "command.py"

    @property
    def version(self) -> str | None:
        path = self.vault_root / ".brain-core" / "VERSION"
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    @property
    def supports_command_interface(self) -> bool:
        parsed = _version_tuple(self.version)
        return parsed is not None and parsed >= CUTOVER_BRAIN_VERSION


@dataclass(frozen=True, slots=True)
class LauncherDiagnosticReporter:
    """Persist launcher command failures into the selected Brain's diagnostics.

    Machine-global invocations with no selected Brain have no diagnostics
    destination; reporting is skipped rather than invented elsewhere.
    """

    vault_root: Path | None

    def report_failure(
        self,
        *,
        phase: str,
        command_id: str,
        correlation_id: str,
        error: BaseException,
    ) -> None:
        if self.vault_root is None:
            return
        from _common import _operational_log

        _operational_log.append_record(
            self.vault_root,
            "cli",
            "command.failed",
            phase=phase,
            command_id=command_id,
            correlation_id=correlation_id,
            error_class=_operational_log.classify_error(error),
            exception_type=type(error).__name__,
        )


class LauncherReceiptStore:
    """Persist actual or uncertain effects; no-effect receipts need no storage."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, receipt: OutcomeReceipt) -> None:
        if receipt.state is ReceiptState.NONE:
            return
        if self._root.is_symlink():
            raise OSError("launcher receipt directory cannot be a symlink")
        self._root.mkdir(parents=True, exist_ok=True)
        destination = self._root / f"{receipt.reference.invocation_id}.json"
        if destination.is_symlink():
            raise OSError("launcher receipt path cannot be a symlink")
        payload = asdict(receipt)
        payload["recorded_at"] = receipt.recorded_at.isoformat()
        payload["state"] = receipt.state.value
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"
        temporary = self._root / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        self._trim()

    def _trim(self) -> None:
        """Best-effort retention so the machine store cannot grow unbounded."""
        try:
            entries = sorted(
                (path for path in self._root.glob("*.json") if path.is_file()),
                key=lambda path: path.stat().st_mtime,
            )
            cutoff = time.time() - _RECEIPT_RETENTION_SECONDS
            retained = []
            for path in entries:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                else:
                    retained.append(path)
            overflow = len(retained) - _RECEIPT_MAX_RECORDS
            for path in retained[: max(0, overflow)]:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def resolve_selected_brain(
    *,
    vault: str | None,
    brain_id: str | None,
    workspace: str | None,
    start_dir: Path | None = None,
) -> SelectedBrain:
    """Resolve one selected local Brain without mutating machine state."""

    start = (start_dir or Path.cwd()).resolve()
    if vault is not None and brain_id is not None:
        raise ValueError("--vault and --brain are mutually exclusive")
    workspace_path = _absolute_path(workspace, start) if workspace else None
    if vault is not None:
        root = _require_brain(_absolute_path(vault, start))
        return SelectedBrain(root, workspace_path, "explicit_vault")
    if brain_id is not None:
        import vault_registry

        resolved = vault_registry.resolve(brain_id)
        if resolved is None:
            raise ValueError(f"registered local Brain not found: {brain_id}")
        return SelectedBrain(_require_brain(Path(resolved)), workspace_path, "explicit_brain")

    from _bootstrap.workspace_binding import resolve_brain_target

    target = resolve_brain_target(
        workspace_env=str(workspace_path) if workspace_path else os.environ.get("BRAIN_WORKSPACE_DIR"),
        vault_root_env=os.environ.get("BRAIN_VAULT_ROOT"),
        start_dir=start,
    )
    return SelectedBrain(
        _require_brain(Path(target.vault_root)),
        Path(target.workspace_dir).resolve() if target.workspace_dir else None,
        target.source,
    )


def resolve_project_exposure_brain(
    *,
    vault: str | None,
    brain_id: str | None,
    workspace: str | None,
    start_dir: Path | None = None,
) -> SelectedBrain:
    """Resolve binding-first/default-second while retaining the project root."""

    start = (start_dir or Path.cwd()).resolve()
    project_value = workspace or os.environ.get("BRAIN_WORKSPACE_DIR")
    project = _absolute_path(project_value, start) if project_value else start
    if vault is not None or brain_id is not None:
        selected = resolve_selected_brain(
            vault=vault,
            brain_id=brain_id,
            workspace=str(project),
            start_dir=start,
        )
        return SelectedBrain(selected.vault_root, project, selected.source)

    from _bootstrap.workspace_binding import resolve_brain_target

    target = resolve_brain_target(
        workspace_env=None,
        vault_root_env=os.environ.get("BRAIN_VAULT_ROOT"),
        start_dir=project,
    )
    return SelectedBrain(
        _require_brain(Path(target.vault_root)),
        project,
        target.source,
    )


def compose_launcher_context(
    *,
    cli_binary: Path,
    distribution_root: Path,
    selected: SelectedBrain | None,
    dry_run: bool,
    operator_key: str | None = None,
) -> LauncherContext:
    invocation_id = f"cli-{uuid.uuid4()}"
    state_home = Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    ).expanduser().resolve()
    return LauncherContext(
        profile="local-operator",
        authority=LocalAuthority(),
        providers=ProviderBindings((LocalProvider("caller_filesystem"),)),
        correlation_id=invocation_id,
        invocation_id=invocation_id,
        receipt_writer=LauncherReceiptStore(state_home / "brain" / "command-outcomes"),
        clock=LocalClock(),
        caller_dir=Path.cwd().resolve(),
        home_dir=Path.home().resolve(),
        cli_version=CLI_VERSION,
        cli_binary=cli_binary.resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=selected.vault_root if selected else None,
        workspace_dir=selected.workspace if selected else None,
        distribution_root=distribution_root.resolve(),
        operator_key=operator_key,
        dry_run=dry_run,
        diagnostics=LauncherDiagnosticReporter(
            selected.vault_root if selected else None
        ),
    )


def command_python(selected: SelectedBrain, dependency_tier: str) -> Path:
    """Choose the selected Brain's managed runtime only when the command needs it."""

    if dependency_tier != "managed":
        return Path(sys.executable).resolve()
    from _common._venv import find_runnable_python

    candidate = find_runnable_python(
        selected.vault_root,
        launcher=Path(sys.executable).resolve(),
    )
    # Preserve the virtual-environment entry point.  Resolving this symlink
    # collapses it to the base interpreter on POSIX, so Python no longer sets
    # ``sys.prefix`` to the managed venv and selected-Brain commands observe
    # only the portable dependency tier.
    return Path(candidate).absolute() if candidate is not None else Path(sys.executable).resolve()


def _absolute_path(value: str, start: Path) -> Path:
    path = Path(value).expanduser()
    candidate = path if path.is_absolute() else start / path
    return Path(os.path.abspath(candidate))


def _require_brain(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.is_symlink():
        raise ValueError(f"not an installed local Brain: {path}")
    root = candidate.resolve()
    version = root / ".brain-core" / "VERSION"
    if version.is_symlink() or not version.is_file():
        raise ValueError(f"not an installed local Brain: {path}")
    return root


def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)
