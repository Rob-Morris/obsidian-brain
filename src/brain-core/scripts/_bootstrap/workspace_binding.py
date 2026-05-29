"""Launcher-safe helpers for workspace-owned Brain binding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import unicodedata
from typing import Any

from _common import is_vault_root, safe_write
from _common._yaml import YamlError, dump_mapping_text, load_mapping_file
import vault_registry


WORKSPACE_MANIFEST_REL = os.path.join(".brain", "local", "workspace.yaml")
WORKSPACE_MANIFEST_LEGACY_REL = os.path.join(".brain", "workspace.yaml")


WORKSPACE_REASON_ALREADY_BOUND = "already_bound"


class WorkspaceBindingError(RuntimeError):
    """Raised when workspace binding state cannot be converged safely."""

    def __init__(self, message: str, *, code: str = "invalid_binding") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceManifestState:
    """Resolved workspace manifest state for one target directory."""

    target_dir: Path
    manifest_path: Path
    legacy_path: Path
    source_path: Path | None
    data: dict[str, Any] | None


@dataclass(frozen=True)
class WorkspaceManifestWrite:
    """Result of writing canonical workspace manifest content."""

    manifest_path: Path
    status: str
    message: str
    migrated_legacy: bool


@dataclass(frozen=True)
class WorkspaceBindingConvergence:
    """Result of converging one workspace binding payload."""

    manifest_path: Path
    brain: str
    slug: str
    status: str
    message: str
    migrated_legacy: bool


def workspace_slug(name: str) -> str:
    """Return a stable slug for a workspace directory name."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name.strip().lower()).strip("-")
    return slug or "workspace"


def manifest_path_for(target_dir: Path) -> Path:
    return target_dir / WORKSPACE_MANIFEST_REL


def legacy_manifest_path_for(target_dir: Path) -> Path:
    return target_dir / WORKSPACE_MANIFEST_LEGACY_REL


def resolve_workspace_dir(path_arg: str | None) -> Path:
    """Resolve a workspace directory argument or default to the current directory."""
    target = Path(path_arg).resolve() if path_arg else Path.cwd().resolve()
    if not target.is_dir():
        raise WorkspaceBindingError(
            f"workspace path is not a directory: {target}",
            code="workspace_path_invalid",
        )
    return target


def load_workspace_manifest_state(target_dir: Path) -> WorkspaceManifestState:
    """Load canonical or legacy workspace manifest state for a target directory."""
    manifest_path = manifest_path_for(target_dir)
    legacy_path = legacy_manifest_path_for(target_dir)

    if manifest_path.is_file():
        try:
            data = load_mapping_file(manifest_path)
        except (OSError, YamlError) as exc:
            raise WorkspaceBindingError(f"failed to load {WORKSPACE_MANIFEST_REL}: {exc}") from exc
        return WorkspaceManifestState(
            target_dir=target_dir,
            manifest_path=manifest_path,
            legacy_path=legacy_path,
            source_path=manifest_path,
            data=data,
        )

    if legacy_path.is_file():
        try:
            data = load_mapping_file(legacy_path)
        except (OSError, YamlError) as exc:
            raise WorkspaceBindingError(f"failed to load {WORKSPACE_MANIFEST_LEGACY_REL}: {exc}") from exc
        return WorkspaceManifestState(
            target_dir=target_dir,
            manifest_path=manifest_path,
            legacy_path=legacy_path,
            source_path=legacy_path,
            data=data,
        )

    return WorkspaceManifestState(
        target_dir=target_dir,
        manifest_path=manifest_path,
        legacy_path=legacy_path,
        source_path=None,
        data=None,
    )


def read_workspace_manifest(target_dir: Path) -> dict[str, Any] | None:
    """Return workspace manifest content when present, else None."""
    return load_workspace_manifest_state(target_dir).data


def extract_workspace_binding(manifest: Any) -> dict[str, str] | None:
    """Return the canonical binding payload when the manifest shape is valid."""
    if not isinstance(manifest, dict):
        return None
    brain = manifest.get("brain")
    slug = manifest.get("slug")
    if not isinstance(brain, str) or not brain:
        return None
    if not isinstance(slug, str) or not slug:
        return None
    return {"brain": brain, "slug": slug}


def require_workspace_binding(target_dir: Path) -> dict[str, str]:
    """Return the canonical binding payload for a bound workspace."""
    manifest = read_workspace_manifest(target_dir)
    if not isinstance(manifest, dict):
        raise WorkspaceBindingError(
            f"{target_dir} is not a bound workspace; missing {WORKSPACE_MANIFEST_REL}"
        )
    binding = extract_workspace_binding(manifest)
    if binding is None:
        brain = manifest.get("brain")
        slug = manifest.get("slug")
        if not isinstance(brain, str) or not brain:
            raise WorkspaceBindingError(
                f"{WORKSPACE_MANIFEST_REL} is missing a valid 'brain' value"
            )
        raise WorkspaceBindingError(
            f"{WORKSPACE_MANIFEST_REL} is missing a valid 'slug' value"
        )
    return binding


def find_bound_workspace_dir(start_dir: Path | None = None) -> Path | None:
    """Walk upward from start_dir (or cwd) for a bound workspace manifest."""
    current = (start_dir or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        state = load_workspace_manifest_state(candidate)
        if state.source_path is not None:
            return candidate
    return None


def resolve_local_brain_alias(vault_root: Path) -> str:
    """Return the authoritative local symbolic Brain ID for a vault."""
    try:
        return vault_registry.backfill(str(vault_root))
    except OSError as exc:
        raise WorkspaceBindingError(
            f"failed to resolve local Brain ID for {vault_root}: {exc}"
        ) from exc


def resolve_bound_brain_vault(brain_id: str) -> Path | None:
    """Resolve a symbolic local Brain ID to a vault path when it exists locally."""
    resolved = vault_registry.resolve(brain_id)
    if not resolved:
        return None
    candidate = Path(resolved).resolve()
    if not is_vault_root(candidate):
        return None
    return candidate


def save_workspace_manifest_data(
    target_dir: Path,
    data: dict[str, Any],
    *,
    state: WorkspaceManifestState | None = None,
) -> WorkspaceManifestWrite:
    """Persist canonical workspace manifest content and migrate legacy paths."""
    state = state or load_workspace_manifest_state(target_dir)
    migrated_legacy = state.source_path == state.legacy_path
    current_text = None
    if state.source_path is not None:
        try:
            current_text = state.source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkspaceBindingError(f"failed to read existing workspace manifest: {exc}") from exc
    next_text = dump_mapping_text(data)

    if current_text == next_text and state.source_path == state.manifest_path:
        return WorkspaceManifestWrite(
            manifest_path=state.manifest_path,
            status="noop",
            message=f"{WORKSPACE_MANIFEST_REL} is already up to date.",
            migrated_legacy=False,
        )

    state.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(state.manifest_path, next_text)
    if state.legacy_path.is_file():
        try:
            state.legacy_path.unlink()
        except OSError as exc:
            raise WorkspaceBindingError(
                f"failed to remove legacy manifest {WORKSPACE_MANIFEST_LEGACY_REL}: {exc}"
            ) from exc

    if state.source_path is None:
        message = f"Created {WORKSPACE_MANIFEST_REL}."
    elif migrated_legacy:
        message = f"Migrated {WORKSPACE_MANIFEST_LEGACY_REL} to {WORKSPACE_MANIFEST_REL}."
    else:
        message = f"Updated {WORKSPACE_MANIFEST_REL}."

    return WorkspaceManifestWrite(
        manifest_path=state.manifest_path,
        status="changed",
        message=message,
        migrated_legacy=migrated_legacy,
    )


def converge_workspace_binding(
    target_dir: Path,
    *,
    brain: str,
    slug: str | None = None,
    allow_rebind: bool,
) -> WorkspaceBindingConvergence:
    """Create or update the canonical workspace binding manifest."""
    state = load_workspace_manifest_state(target_dir)
    existing = dict(state.data or {})
    existing_brain = existing.get("brain")
    existing_slug = existing.get("slug")

    resolved_slug = slug or (existing_slug if isinstance(existing_slug, str) and existing_slug else None)
    if not resolved_slug:
        resolved_slug = workspace_slug(target_dir.name)

    if existing_brain and existing_brain != brain and not allow_rebind:
        raise WorkspaceBindingError(
            f"{WORKSPACE_MANIFEST_REL} already binds this workspace to '{existing_brain}'. "
            "Use `configure workspace binding` to change it.",
            code=WORKSPACE_REASON_ALREADY_BOUND,
        )
    if slug is not None and existing_slug and existing_slug != slug and not allow_rebind:
        raise WorkspaceBindingError(
            f"{WORKSPACE_MANIFEST_REL} already records slug '{existing_slug}'. "
            "Use `configure workspace binding` to change it.",
            code=WORKSPACE_REASON_ALREADY_BOUND,
        )

    payload = _binding_payload(existing, brain=brain, slug=resolved_slug)
    write = save_workspace_manifest_data(target_dir, payload, state=state)
    return WorkspaceBindingConvergence(
        manifest_path=write.manifest_path,
        brain=brain,
        slug=resolved_slug,
        status=write.status,
        message=write.message,
        migrated_legacy=write.migrated_legacy,
    )


def _binding_payload(existing: dict[str, Any], *, brain: str, slug: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "brain": brain,
        "slug": slug,
    }
    for key, value in existing.items():
        if key in {"brain", "slug"}:
            continue
        payload[key] = value
    return payload
