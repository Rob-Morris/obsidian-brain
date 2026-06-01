"""Launcher-safe helpers for workspace-owned Brain binding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import os
import re
import unicodedata
from typing import Any

from _common import safe_write
from _common._yaml import YamlError, dump_mapping_text, load_mapping_file
import vault_registry

_log = logging.getLogger(__name__)


def is_brain_vault(path: Path) -> bool:
    """Return True when *path* is a resolvable Brain vault root.

    A resolvable vault has a ``.brain-core/VERSION`` file — the resolver
    re-points ``PYTHONPATH`` at that ``.brain-core``. This is intentionally
    narrower than ``_common.is_vault_root`` (which also accepts an
    ``AGENTS.md``-only bootstrap directory for CLI discovery): an
    ``AGENTS.md``-only *workspace* must never resolve as vault-self, be accepted
    as a ``BRAIN_VAULT_ROOT``/registry target, or be refused as "a workspace of
    itself". This narrow predicate is the single source of truth for every
    resolution, heal, and refuse-guard decision in this module.
    """
    p = path if isinstance(path, Path) else Path(path)
    return (p / ".brain-core" / "VERSION").is_file()


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

WORKSPACE_MANIFEST_REL = os.path.join(".brain", "local", "workspace.yaml")
WORKSPACE_MANIFEST_LEGACY_REL = os.path.join(".brain", "workspace.yaml")

WORKSPACE_REASON_ALREADY_BOUND = "already_bound"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WorkspaceBindingError(RuntimeError):
    """Raised when workspace binding state cannot be converged safely."""

    def __init__(self, message: str, *, code: str = "invalid_binding") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Manifest-state dataclasses
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Resolution ladder — BrainTarget + resolve_brain_target
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BrainTarget:
    """Resolved Brain target produced by the unified resolution ladder.

    Fields
    ------
    vault_root:
        Absolute path to the resolved vault root (str, not Path, for env compat).
    workspace_dir:
        Absolute path to the bound workspace directory when the resolution came
        from a workspace binding, else None.
    source:
        Short tag identifying which rung resolved the target.  One of:
        'workspace_env' | 'vault_self' | 'workspace_binding' |
        'vault_root_env' | 'registry_default'.
    """

    vault_root: str
    workspace_dir: str | None
    source: str


# Binding-state constants used by the classifier helper.
_STATE_VALID = "valid"
_STATE_STALE = "stale"
_STATE_MISSING = "missing"


def _classify_workspace_binding(ws_dir: Path) -> tuple[str, Path | None]:
    """Classify a workspace directory's Brain binding state.

    Returns a ``(state, vault)`` tuple where ``state`` is one of the
    ``_STATE_*`` constants.

    Classification is based solely on the presence/resolvability of the
    ``brain`` key — the ``slug`` key is not required here.  This avoids
    conflating "no brain key" with "missing slug" in resolution paths.

    Args:
        ws_dir: The workspace directory to classify.

    Returns:
        - ``(_STATE_VALID, <vault_path>)`` when the brain key is present and
          resolves to a live vault root.
        - ``(_STATE_STALE, None)`` when the brain key is present but cannot be
          resolved to a live vault root.
        - ``(_STATE_MISSING, None)`` when there is no manifest or no brain key.
    """
    manifest = read_workspace_manifest(ws_dir)
    if not isinstance(manifest, dict):
        return _STATE_MISSING, None
    brain = manifest.get("brain")
    if not isinstance(brain, str) or not brain:
        return _STATE_MISSING, None
    vault = resolve_local_brain_vault(brain)
    if vault is None:
        return _STATE_STALE, None
    return _STATE_VALID, vault


def _walk_for_nearest_marker(start_dir: Path) -> BrainTarget | None:
    """Walk upward from *start_dir* for the nearest Brain marker and return a
    ``BrainTarget`` when found, or raise ``WorkspaceBindingError`` when stale.

    Marker detection per directory:

    1. ``(dir / ".brain-core" / "VERSION").is_file()`` — vault root present
       here → 'vault_self'.  Gated on ``.brain-core/VERSION`` specifically
       rather than the broad ``_common.is_vault_root`` so that AGENTS.md-only directories
       (e.g. a repo root) are not misidentified as vault roots.
       This wins over a co-located workspace.yaml in the same directory.
    2. ``load_workspace_manifest_state(dir).source_path is not None`` —
       workspace manifest present → classify its binding:
       - VALID  → return BrainTarget('workspace_binding').
       - STALE  → raise (wrong-brain hazard).
       - MISSING (manifest exists but no brain key) → stop walking, return None
         so the caller falls through to lower rungs.

    The first directory that contains either marker terminates the walk.  A
    vault-root marker takes priority over a co-located workspace marker in the
    same directory.

    Returns:
        A ``BrainTarget`` on success, or ``None`` when no marker was found or
        when a marker with a MISSING state was found (fall through to rung 3).

    Raises:
        ``WorkspaceBindingError`` when a STALE binding is encountered.
    """
    current = start_dir.resolve()
    for candidate in (current, *current.parents):
        # Vault-root check takes priority over a co-located workspace.yaml.
        # is_brain_vault gates on .brain-core/VERSION specifically — the broad
        # _common.is_vault_root also matches AGENTS.md-only dirs (e.g. this repo
        # root), which must NOT resolve as vault_self (wrong-brain hazard).
        if is_brain_vault(candidate):
            return BrainTarget(
                vault_root=str(candidate),
                workspace_dir=None,
                source="vault_self",
            )

        # Workspace manifest check.
        state = load_workspace_manifest_state(candidate)
        if state.source_path is not None:
            # A manifest exists — classify by the brain key.
            binding_state, vault = _classify_workspace_binding(candidate)
            if binding_state == _STATE_VALID:
                assert vault is not None
                return BrainTarget(
                    vault_root=str(vault),
                    workspace_dir=str(candidate),
                    source="workspace_binding",
                )
            if binding_state == _STATE_STALE:
                brain = (state.data or {}).get("brain", "<unknown>")
                raise WorkspaceBindingError(
                    f"workspace at {candidate} is bound to Brain '{brain}' which "
                    f"cannot be resolved — re-bind or repair this workspace "
                    f"(brain setup workspace) before continuing.",
                    code="stale_binding",
                )
            # MISSING — manifest present but no brain key.  Stop the walk;
            # fall through to rung 3 rather than crossing into an unrelated
            # workspace further up the tree (wrong-brain hazard).
            return None

    return None


def resolve_brain_target(
    *,
    workspace_env: str | None,
    vault_root_env: str | None,
    start_dir: Path,
) -> BrainTarget:
    """Resolve the active Brain target using the unified precedence ladder.

    PURITY GUARANTEE: this function is free of side effects.  It performs
    reads (filesystem, registry) but never writes to ``os.environ``, the
    vault registry, or any manifest.  Callers are responsible for applying
    the result to the environment.

    Ladder (exact precedence)
    -------------------------
    1. ``BRAIN_WORKSPACE_DIR`` set → consult ONLY that workspace's binding:
       - VALID  → use it.
       - STALE  → raise ``WorkspaceBindingError`` (STOP; do NOT fall through).
       - MISSING (no brain key) → skip rung 2 entirely; jump straight to rung 3.
         An explicit anchor must never cross-resolve a different workspace found
         by the cwd walk — wrong-brain hazard.
    2. Only when ``BRAIN_WORKSPACE_DIR`` is unset — walk upward from *start_dir*:
       - Nearest vault root → resolve by path ('vault_self').
       - Nearest workspace manifest → classify:
         VALID → use ('workspace_binding'); STALE → raise (STOP); MISSING → rung 3.
    3. ``BRAIN_VAULT_ROOT`` set and ``is_brain_vault`` → use ('vault_root_env').
    4. ``vault_registry.get_default()`` set → resolve:
       resolves → use ('registry_default'); dangling → raise (STOP).
    5. Nothing → raise with the setup cue.

    Args:
        workspace_env:   Value of the ``BRAIN_WORKSPACE_DIR`` env var, or None.
        vault_root_env:  Value of the ``BRAIN_VAULT_ROOT`` env var, or None.
        start_dir:       Directory from which to begin the rung-2 upward walk
                         (typically ``Path.cwd()`` in production callers).

    Returns:
        A ``BrainTarget`` describing the resolved vault.

    Raises:
        ``WorkspaceBindingError`` on stale bindings, dangling defaults, or when
        no brain can be resolved at all.
    """
    # ------------------------------------------------------------------
    # Rung 1: explicit workspace anchor
    # ------------------------------------------------------------------
    if workspace_env:
        ws_dir = Path(workspace_env).resolve()
        # Vault-self short-circuit: when BRAIN_WORKSPACE_DIR points at the
        # vault root itself, resolve by path immediately — no binding lookup.
        if is_brain_vault(ws_dir):
            return BrainTarget(
                vault_root=str(ws_dir),
                workspace_dir=None,
                source="vault_self",
            )
        state, vault = _classify_workspace_binding(ws_dir)
        if state == _STATE_VALID:
            assert vault is not None
            return BrainTarget(
                vault_root=str(vault),
                workspace_dir=str(ws_dir),
                source="workspace_env",
            )
        if state == _STATE_STALE:
            manifest = read_workspace_manifest(ws_dir)
            brain = (
                (manifest or {}).get("brain", "<unknown>")
                if isinstance(manifest, dict)
                else "<unknown>"
            )
            raise WorkspaceBindingError(
                f"BRAIN_WORKSPACE_DIR points to a workspace bound to Brain "
                f"'{brain}' which cannot be resolved — re-bind or repair this "
                f"workspace (brain setup workspace) before continuing.",
                code="stale_binding",
            )
        # MISSING — skip rung 2, jump straight to rung 3.

    else:
        # ------------------------------------------------------------------
        # Rung 2: cwd walk (only when workspace_env is unset)
        # ------------------------------------------------------------------
        target = _walk_for_nearest_marker(start_dir)
        if target is not None:
            return target
        # target is None → MISSING marker or no marker found; continue to rung 3.

    # ------------------------------------------------------------------
    # Rung 3: BRAIN_VAULT_ROOT env var
    # ------------------------------------------------------------------
    if vault_root_env:
        vault_path = Path(vault_root_env)
        if is_brain_vault(vault_path):
            return BrainTarget(
                vault_root=str(vault_path.resolve()),
                workspace_dir=None,
                source="vault_root_env",
            )

    # ------------------------------------------------------------------
    # Rung 4: machine-wide registry default
    # ------------------------------------------------------------------
    try:
        default_id = vault_registry.get_default()
    except vault_registry.RegistryReadError as exc:
        raise WorkspaceBindingError(
            f"failed to read Brain registry default: {exc}"
        ) from exc

    if default_id:
        vault = resolve_local_brain_vault(default_id)
        if vault is not None:
            return BrainTarget(
                vault_root=str(vault),
                workspace_dir=None,
                source="registry_default",
            )
        raise WorkspaceBindingError(
            f"the machine default Brain '{default_id}' is registered but its "
            f"vault directory cannot be found — re-register or remove the "
            f"default (vault_registry --clear-default).",
            code="stale_binding",
        )

    # ------------------------------------------------------------------
    # Rung 5: nothing resolved
    # ------------------------------------------------------------------
    raise WorkspaceBindingError(
        "no Brain could be resolved — bind this workspace "
        "(brain setup workspace) or set a machine default "
        "(vault_registry --set-default).",
        code="no_brain",
    )


# ---------------------------------------------------------------------------
# Self-heal — best-effort, idempotent, missing-only
# ---------------------------------------------------------------------------

def heal_legacy_config(
    target: BrainTarget,
    *,
    workspace_env: str | None,
    vault_root_env: str | None,
) -> None:
    """Best-effort, idempotent migration of legacy Brain config state.

    Runs AFTER a successful (non-stale) resolution.  Both triggers are
    INDEPENDENT ``if`` blocks — not ``if/elif`` — so they can co-fire when
    both conditions hold (e.g. ``vault_self`` source with a legacy
    ``BRAIN_VAULT_ROOT`` set).

    Trigger (1) — SELF-REGISTER
        When source is "vault_self", backfill the vault into the registry.
        This is idempotent: ``vault_registry.backfill`` returns the existing
        Brain ID when the path is already registered.

    Trigger (2) — LEGACY BRAIN_VAULT_ROOT signals
        Guarded by ``vault_root_env``.  Inner branches are mutually exclusive
        on ``workspace_env``:

        PROJECT REG (``workspace_env`` set, source=="vault_root_env")
            The anchor binding was MISSING (a stale one would have raised at
            rung 1).  Register the vault and write the workspace binding.
            ``allow_rebind=False`` ensures we only write when the binding is
            absent — never overwrite an existing binding.

        USER REG default-seed (``workspace_env`` not set)
            Seed the machine default from the *env* value, never from
            ``target.vault_root``.  When cd'd into a different bound vault,
            ``target.vault_root`` is that other brain; the user-reg default
            must come from ``vault_root_env``.  Only seeds when no default is
            already set.
    """
    # (1) SELF-REGISTER — independent check; no return after this block.
    if target.source == "vault_self":
        try:
            vault_registry.backfill(target.vault_root)
        except Exception as exc:
            _log.warning("heal_legacy_config: self-register backfill failed: %s", exc)

    # (2) LEGACY BRAIN_VAULT_ROOT signals — guarded by vault_root_env.
    if vault_root_env:
        if workspace_env and target.source == "vault_root_env":
            # PROJECT REG: the anchor binding was MISSING; write it now.
            try:
                brain_id = vault_registry.register(target.vault_root)
                converge_workspace_binding(
                    Path(workspace_env),
                    brain=brain_id,
                    allow_rebind=False,
                )
            except Exception as exc:
                _log.warning("heal_legacy_config: project-reg binding failed: %s", exc)
        elif not workspace_env:
            # USER REG default-seed: seed from vault_root_env, NOT target.vault_root.
            try:
                resolved = Path(vault_root_env).resolve()
                if is_brain_vault(resolved):
                    brain_id = vault_registry.register(str(resolved))
                    if vault_registry.get_default() is None:
                        vault_registry.set_default(brain_id)
            except Exception as exc:
                _log.warning("heal_legacy_config: user-reg default-seed failed: %s", exc)


def resolve_and_heal(
    *,
    workspace_env: str | None,
    vault_root_env: str | None,
    start_dir: Path,
) -> BrainTarget:
    """Resolve the active Brain target, then run best-effort self-heal.

    Resolution MUST succeed (and is pure/non-mutating) before any heal runs.
    Because ``resolve_brain_target`` raises on stale or missing bindings,
    ``heal_legacy_config`` is never reached on the stale path.

    Args:
        workspace_env:   Value of the ``BRAIN_WORKSPACE_DIR`` env var, or None.
        vault_root_env:  Value of the ``BRAIN_VAULT_ROOT`` env var, or None.
        start_dir:       Directory from which to begin the rung-2 upward walk.

    Returns:
        The resolved ``BrainTarget``.

    Raises:
        ``WorkspaceBindingError`` on stale bindings, dangling defaults, or when
        no brain can be resolved at all.  Heal errors are caught and logged;
        they never propagate.
    """
    target = resolve_brain_target(
        workspace_env=workspace_env,
        vault_root_env=vault_root_env,
        start_dir=start_dir,
    )
    try:
        heal_legacy_config(
            target,
            workspace_env=workspace_env,
            vault_root_env=vault_root_env,
        )
    except Exception as exc:  # pragma: no cover — heal errors are best-effort
        _log.warning("resolve_and_heal: heal_legacy_config raised unexpectedly: %s", exc)
    return target


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

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
    """Return the authoritative local symbolic Brain ID for a vault.

    This comes from the user-home vault registry, not from the derived
    machine registry in ``brains.json``.
    """
    try:
        return vault_registry.backfill(str(vault_root))
    except (OSError, vault_registry.RegistryReadError) as exc:
        raise WorkspaceBindingError(
            f"failed to resolve local Brain ID for {vault_root}: {exc}"
        ) from exc


def resolve_local_brain_vault(brain_id: str) -> Path | None:
    """Resolve a symbolic local Brain ID via the authoritative local vault registry."""
    try:
        resolved = vault_registry.resolve(brain_id)
    except vault_registry.RegistryReadError as exc:
        raise WorkspaceBindingError(
            f"failed to read local Brain registry while resolving Brain ID '{brain_id}': {exc}"
        ) from exc
    if not resolved:
        return None
    candidate = Path(resolved).resolve()
    if not is_brain_vault(candidate):
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
    # Refuse-guard: a vault root is a Brain, not a workspace of itself.
    # It resolves by path (vault_self) — binding it would create a circular
    # reference.  The vault-self MCP mode (apply_mcp_transport_action with
    # vault_self=True) skips this function intentionally.
    if is_brain_vault(target_dir):
        raise WorkspaceBindingError(
            f"{target_dir} is a Brain vault root, not a workspace of itself. "
            "It resolves by path — do not bind it as a workspace. "
            "Use vault-self MCP registration instead.",
            code="vault_root_not_workspace",
        )
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
