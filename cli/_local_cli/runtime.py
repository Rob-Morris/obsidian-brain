"""Concrete machine-local composition for the Brain CLI 2 application."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import OutcomeReceipt


CLI_VERSION = "2.1.0"
CUTOVER_BRAIN_VERSION = (0, 55, 0)


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


class LauncherReceiptStore:
    """Privacy-minimal durable machine receipt writer."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write(self, receipt: OutcomeReceipt) -> None:
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
        distribution_root=distribution_root.resolve(),
        operator_key=operator_key,
        dry_run=dry_run,
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
    return Path(candidate).resolve() if candidate is not None else Path(sys.executable).resolve()


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
