"""Launcher-safe helpers for workspace-owned Brain binding."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import io
import os
import re
import unicodedata
from typing import Any

from _common._filesystem import safe_write
from _common._vault import is_brain_vault
from _common._slugs import is_valid_key
from _common._yaml import YamlError, dump_mapping_text, load_mapping_file, load_mapping_text
import vault_registry


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

WORKSPACE_MANIFEST_REL = os.path.join(".brain", "local", "workspace.yaml")
WORKSPACE_MANIFEST_LEGACY_REL = os.path.join(".brain", "workspace.yaml")

WORKSPACE_REASON_ALREADY_BOUND = "already_bound"
WORKSPACE_ERROR_INVALID_BINDING = "invalid_binding"
WORKSPACE_ERROR_FILESYSTEM_ACCESS = "filesystem_access"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WorkspaceBindingError(RuntimeError):
    """Raised when workspace binding state cannot be converged safely.

    ``rung`` names the resolution-ladder rung that failed (a ``BrainTarget.source``
    value, or ``RUNG_UNRESOLVED``), and is None outside ``resolve_brain_target``.
    """

    def __init__(self, message: str, *, code: str = WORKSPACE_ERROR_INVALID_BINDING,
                 rung: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.rung = rung


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


# The ladder's rungs, in order; ``BrainTarget.source`` names the one that resolved a Brain
# (``vault_self`` is the first rung's short-circuit) and ``WorkspaceBindingError.rung`` the one that failed.
RUNG_VAULT_SELF = "vault_self"
RUNG_WORKSPACE_ENV = "workspace_env"
RUNG_WORKSPACE_BINDING = "workspace_binding"
RUNG_VAULT_ROOT_ENV = "vault_root_env"
RUNG_REGISTRY_DEFAULT = "registry_default"
# The ladder's last rung: nothing resolved a Brain.
RUNG_UNRESOLVED = "unresolved"
# The rungs no caller asserted: the machine default and nothing at all.
MACHINE_FALLBACK_RUNGS = frozenset({RUNG_REGISTRY_DEFAULT, RUNG_UNRESOLVED})


@contextmanager
def _rung(name: str):
    """Attribute a resolution failure raised inside one ladder rung to that rung."""
    try:
        yield
    except WorkspaceBindingError as exc:
        if exc.rung is None:
            exc.rung = name
        raise


# Binding-state constants used by the classifier helper.
_STATE_VALID = "valid"
_STATE_STALE = "stale"
_STATE_MISSING = "missing"


def _classify_workspace_binding(ws_dir: Path) -> tuple[str, Path | None, str | None]:
    """Classify a workspace directory's Brain binding state.

    Returns a ``(state, vault, brain_id)`` tuple where ``state`` is one of the
    ``_STATE_*`` constants and ``brain_id`` is the declared id when present — so
    callers need not re-read the manifest to build the stale-binding message.

    Classification is based solely on the presence/resolvability of the
    ``brain`` key — the ``slug`` key is not required here.  This avoids
    conflating "no brain key" with "missing slug" in resolution paths.

    Args:
        ws_dir: The workspace directory to classify.

    Returns:
        - ``(_STATE_VALID, <vault_path>, <brain_id>)`` — brain key present and
          resolves to a live vault root.
        - ``(_STATE_STALE, None, <brain_id>)`` — brain key present but cannot be
          resolved to a live vault root.
        - ``(_STATE_MISSING, None, None)`` — no manifest or no brain key.
    """
    manifest = read_workspace_manifest(ws_dir)
    if not isinstance(manifest, dict):
        return _STATE_MISSING, None, None
    brain = manifest.get("brain")
    if not isinstance(brain, str) or not brain:
        return _STATE_MISSING, None, None
    vault = resolve_local_brain_vault(brain)
    if vault is None:
        return _STATE_STALE, None, brain
    return _STATE_VALID, vault, brain


def _stale_binding_detail(brain: str) -> str:
    """Return a specific diagnostic for an unresolvable Brain id, distinguishing
    "not in the registry" from "registered but its vault is missing/moved" so the
    stale-binding prompt is actionable."""
    try:
        registered = vault_registry.resolve(brain)
    except vault_registry.RegistryReadError:
        registered = None
    if registered is None:
        return f"Brain id '{brain}' is not in the registry"
    if not vault_registry.is_canonical_value(registered):
        entries = vault_registry.load_registry_entries()
        return vault_registry.stale_explanation(entries[brain], entries)
    return f"Brain '{brain}' is registered but its vault at {registered} is missing or moved"


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
                source=RUNG_VAULT_SELF,
            )

        # Workspace manifest check.
        state = load_workspace_manifest_state(candidate)
        if state.source_path is not None:
            # A manifest exists — classify by the brain key.
            binding_state, vault, brain = _classify_workspace_binding(candidate)
            if binding_state == _STATE_VALID:
                assert vault is not None
                return BrainTarget(
                    vault_root=str(vault),
                    workspace_dir=str(candidate),
                    source=RUNG_WORKSPACE_BINDING,
                )
            if binding_state == _STATE_STALE:
                assert brain is not None
                raise WorkspaceBindingError(
                    f"workspace at {candidate} cannot be resolved: "
                    f"{_stale_binding_detail(brain)} — re-bind or repair this "
                    f"workspace (brain workspace setup), or restore the registry "
                    f"entry, before continuing.",
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
       - MISSING (no brain key) → skip rung 2; try rung 3 (BRAIN_VAULT_ROOT) only.
         If rung 3 does not resolve, HARD-ERROR — never fall to the rung-4
         default (Decision #2): an explicit anchor must not cross-resolve a
         different workspace, nor be silently served the machine default.
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
        no brain can be resolved at all; its ``rung`` names the rung that failed.
    """
    # ------------------------------------------------------------------
    # Rung 1: explicit workspace anchor
    # ------------------------------------------------------------------
    anchor_missing = False
    if workspace_env:
        ws_dir = Path(workspace_env).resolve()
        # Vault-self short-circuit: when BRAIN_WORKSPACE_DIR points at the
        # vault root itself, resolve by path immediately — no binding lookup.
        if is_brain_vault(ws_dir):
            return BrainTarget(
                vault_root=str(ws_dir),
                workspace_dir=None,
                source=RUNG_VAULT_SELF,
            )
        with _rung(RUNG_WORKSPACE_ENV):
            state, vault, brain = _classify_workspace_binding(ws_dir)
        if state == _STATE_VALID:
            assert vault is not None
            return BrainTarget(
                vault_root=str(vault),
                workspace_dir=str(ws_dir),
                source=RUNG_WORKSPACE_ENV,
            )
        if state == _STATE_STALE:
            assert brain is not None
            raise WorkspaceBindingError(
                f"BRAIN_WORKSPACE_DIR points to a workspace that cannot be "
                f"resolved: {_stale_binding_detail(brain)} — re-bind or repair "
                f"this workspace (brain workspace setup), or restore the registry "
                f"entry, before continuing.",
                code="stale_binding",
                rung=RUNG_WORKSPACE_ENV,
            )
        # MISSING — the explicit anchor's binding is absent.  It may still
        # resolve through BRAIN_VAULT_ROOT (rung 3), but it must NOT fall to
        # the machine default (rung 4): a deliberately-bound
        # workspace whose binding is lost has a specific, now-unknowable intent,
        # and the default could serve a different Brain (Decision #2).
        anchor_missing = True

    else:
        # ------------------------------------------------------------------
        # Rung 2: cwd walk (only when workspace_env is unset)
        # ------------------------------------------------------------------
        with _rung(RUNG_WORKSPACE_BINDING):
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
                source=RUNG_VAULT_ROOT_ENV,
            )

    # ------------------------------------------------------------------
    # Decision #2: an explicit anchor with a missing binding and no usable
    # BRAIN_VAULT_ROOT hard-errors here — it must never fall through to the
    # machine default, which could route to a different Brain than the one
    # this workspace was bound to.
    # ------------------------------------------------------------------
    if anchor_missing:
        raise WorkspaceBindingError(
            f"BRAIN_WORKSPACE_DIR is set ({workspace_env}) but that workspace "
            f"has no Brain binding and no BRAIN_VAULT_ROOT is available to "
            f"resolve it — re-bind this workspace (brain workspace setup) before "
            f"continuing.",
            code="no_brain",
            rung=RUNG_WORKSPACE_ENV,
        )

    # ------------------------------------------------------------------
    # Rung 4: machine-wide registry default
    # ------------------------------------------------------------------
    try:
        default_id = vault_registry.get_default()
    except vault_registry.RegistryReadError as exc:
        raise WorkspaceBindingError(
            f"failed to read Brain registry default: {exc}",
            code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
            rung=RUNG_REGISTRY_DEFAULT,
        ) from exc

    if default_id:
        with _rung(RUNG_REGISTRY_DEFAULT):
            vault = resolve_local_brain_vault(default_id)
        if vault is not None:
            return BrainTarget(
                vault_root=str(vault),
                workspace_dir=None,
                source=RUNG_REGISTRY_DEFAULT,
            )
        raise WorkspaceBindingError(
            f"the machine default Brain cannot be resolved: "
            f"{_stale_binding_detail(default_id)} — re-register it or clear the "
            f"default (brain clear-default).",
            code="stale_binding",
            rung=RUNG_REGISTRY_DEFAULT,
        )

    # ------------------------------------------------------------------
    # Rung 5: nothing resolved
    # ------------------------------------------------------------------
    raise WorkspaceBindingError(
        "no Brain could be resolved — bind this workspace "
        "(brain workspace setup) or set a machine default "
        "(brain set-default --request-json '{\"brain_id\": \"<id>\"}').",
        code="no_brain",
        rung=RUNG_UNRESOLVED,
    )


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
        except OSError as exc:
            raise WorkspaceBindingError(
                f"failed to load {WORKSPACE_MANIFEST_REL}: {exc}",
                code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
            ) from exc
        except YamlError as exc:
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
        except OSError as exc:
            raise WorkspaceBindingError(
                f"failed to load {WORKSPACE_MANIFEST_LEGACY_REL}: {exc}",
                code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
            ) from exc
        except YamlError as exc:
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


def resolve_local_brain_alias(vault_root: Path) -> str | None:
    """Return the vault registry's Brain ID for a vault, or None when it is unregistered.

    A pure lookup: an unregistered vault stays unregistered.
    """
    try:
        return vault_registry.brain_id_for_path(str(vault_root))
    except vault_registry.RegistryReadError as exc:
        raise WorkspaceBindingError(
            f"failed to resolve local Brain ID for {vault_root}: {exc}",
            code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
        ) from exc


def unregistered_brain_message(vault_root: Path) -> str:
    """Refusal for an operation that needs the Brain ID of an unregistered vault."""
    return (f"the Brain at {vault_root} is not registered on this machine; "
            f"run {vault_registry.register_guidance(vault_root)} first")


def resolve_local_brain_vault(brain_id: str) -> Path | None:
    """Resolve a symbolic local Brain ID via the authoritative local vault registry."""
    try:
        resolved = vault_registry.resolve(brain_id)
    except vault_registry.RegistryReadError as exc:
        raise WorkspaceBindingError(
            f"failed to read local Brain registry while resolving Brain ID '{brain_id}': {exc}",
            code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
        ) from exc
    # A row that is no longer canonical never resolves: it is stale, not a way to follow a symlink.
    if not resolved or not vault_registry.is_canonical_value(resolved):
        return None
    candidate = Path(resolved)
    if not is_brain_vault(candidate):
        return None
    return candidate


def resolve_selected_workspace_binding(vault_root, router, manifest):
    """Require the local alias to identify the selected Brain before resolving policy."""
    from _common._workspace import resolve_workspace_binding

    error = None
    if manifest and isinstance(manifest.get("brain"), str) and manifest["brain"].strip():
        try:
            bound_root = resolve_local_brain_vault(manifest["brain"])
            if bound_root is None or bound_root != Path(vault_root).resolve():
                error = (f"Workspace Brain alias {manifest['brain']!r} does not resolve to the selected Brain. "
                         "Select the bound Brain or run brain workspace setup for the selected Brain.")
        except WorkspaceBindingError as exc:
            error = f"{exc}; repair the Brain registration, then run brain workspace setup."
    return resolve_workspace_binding(router, manifest, brain_binding_error=error)


def save_workspace_manifest_data(
    target_dir: Path,
    data: dict[str, Any],
    *,
    state: WorkspaceManifestState | None = None,
    before_write=None,
) -> WorkspaceManifestWrite:
    """Persist canonical workspace manifest content and migrate legacy paths."""
    state = state or load_workspace_manifest_state(target_dir)
    migrated_legacy = state.source_path == state.legacy_path
    current_text = None
    if state.source_path is not None:
        try:
            current_text = state.source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorkspaceBindingError(
                f"failed to read existing workspace manifest: {exc}",
                code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
            ) from exc
    next_text = dump_mapping_text(data)

    if current_text == next_text and state.source_path == state.manifest_path:
        return WorkspaceManifestWrite(
            manifest_path=state.manifest_path,
            status="noop",
            message=f"{WORKSPACE_MANIFEST_REL} is already up to date.",
            migrated_legacy=False,
        )

    if before_write is not None:
        before_write()
    state.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    safe_write(state.manifest_path, next_text)
    if state.legacy_path.is_file():
        try:
            state.legacy_path.unlink()
        except OSError as exc:
            raise WorkspaceBindingError(
                f"failed to remove legacy manifest {WORKSPACE_MANIFEST_LEGACY_REL}: {exc}",
                code=WORKSPACE_ERROR_FILESYSTEM_ACCESS,
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


def plan_workspace_binding(target_dir, *, brain, slug=None, allow_rebind=False):
    """Validate and resolve a binding without writing either boundary."""
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

    if allow_rebind and existing_brain and (existing_brain != brain or slug is not None and slug != existing_slug):
        from _bootstrap import mcp_registration
        from _bootstrap.file_transaction import FilePlan

        old_vault = resolve_local_brain_vault(existing_brain)
        if old_vault is not None:
            try:
                mcp_registration.require_no_registered_integrations(old_vault, target_dir)
            except (OSError, ValueError) as exc:
                raise WorkspaceBindingError(str(exc)) from exc
        probe = FilePlan()
        for native_scope in (mcp_registration.McpScope.PROJECT, mcp_registration.McpScope.LOCAL):
            for client in mcp_registration._clients(mcp_registration.McpClient.ALL, native_scope):
                path = mcp_registration._config_path(client, native_scope, target_dir, Path.home())
                if mcp_registration.observed_server(probe, client, path) is not None:
                    raise WorkspaceBindingError("Remove the existing workspace MCP integrations before rebinding; configuration was preserved")

    resolved_slug = slug or (existing_slug if isinstance(existing_slug, str) and existing_slug else None)
    if not resolved_slug:
        resolved_slug = workspace_slug(target_dir.name)

    if existing_brain and existing_brain != brain and not allow_rebind:
        raise WorkspaceBindingError(
            f"{WORKSPACE_MANIFEST_REL} already binds this workspace to '{existing_brain}'. "
            "Use `brain workspace setup --request-json '{\"force\": true}'` to change it.",
            code=WORKSPACE_REASON_ALREADY_BOUND,
        )
    if slug is not None and existing_slug and existing_slug != slug and not allow_rebind:
        raise WorkspaceBindingError(
            f"{WORKSPACE_MANIFEST_REL} already records slug '{existing_slug}'. "
            "Use `brain workspace setup --request-json '{\"force\": true}'` to change it.",
            code=WORKSPACE_REASON_ALREADY_BOUND,
        )

    payload = _binding_payload(existing, brain=brain, slug=resolved_slug)
    return state, payload


# ---------------------------------------------------------------------------
# The workspace link: the two manifest fields that state it, and one verdict
# on whether a folder's manifest is the workspace end of a Brain's row.
# ---------------------------------------------------------------------------

# ``brain`` names the Brain and ``links.workspace`` the hub key. The ``slug``,
# defaults and other links are local and never part of the link.
LINK_FIELDS = ("brain", "links.workspace")


def link_fields(manifest: dict[str, Any]) -> tuple[Any, Any]:
    """The manifest's ``brain`` and ``links.workspace`` values, ``None`` where absent."""
    links = manifest.get("links")
    return manifest.get("brain"), links.get("workspace") if isinstance(links, dict) else None


def states_a_link(manifest: dict[str, Any]) -> bool:
    """Whether the manifest holds either link field, valid or not."""
    links = manifest.get("links")
    return "brain" in manifest or isinstance(links, dict) and "workspace" in links


def with_links(manifest: dict[str, Any], links: dict[str, Any]) -> dict[str, Any]:
    """The manifest with ``links`` replaced, dropping the key when no link remains."""
    updated = {name: value for name, value in manifest.items() if name != "links"}
    if links:
        updated["links"] = links
    return updated


def linked_payload(manifest: dict[str, Any], *, key: str) -> dict[str, Any]:
    """The manifest with its hub key set; ``brain`` and ``slug`` come from ``plan_workspace_binding``."""
    links = manifest.get("links", {})
    if not isinstance(links, dict):
        raise ValueError("Workspace links must be a mapping")
    return with_links(manifest, {**links, "workspace": key})


def unlinked_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest without either link field, keeping ``slug``, defaults and other links."""
    links = manifest.get("links", {})
    remaining = {name: value for name, value in links.items() if name != "workspace"} if isinstance(links, dict) else links
    return with_links({name: value for name, value in manifest.items() if name != "brain"}, remaining)


class LinkVerdict(str, Enum):
    MATCHES = "matches"
    UNREACHABLE = "unreachable"
    VAULT_ROOT = "vault_root"
    NO_MANIFEST = "no_manifest"
    UNREADABLE = "unreadable"
    BRAIN_UNRESOLVED = "brain_unresolved"
    KEY_MISSING = "key_missing"
    KEY_INVALID = "key_invalid"
    OTHER_BRAIN = "other_brain"
    OTHER_KEY = "other_key"


# Only these two verdicts positively contradict a row; every other one proves nothing.
LINK_DISAGREEMENT = frozenset({LinkVerdict.OTHER_BRAIN, LinkVerdict.OTHER_KEY})


@dataclass(frozen=True, eq=False)
class ManifestSnapshot:
    """The bytes of a folder's canonical and legacy manifests from one read, ``None`` where a file is absent.

    A read that failed records why in ``failure``, outside the bytes, and
    confirms nothing: it is never the same as another read, failed or not.
    """

    canonical: bytes | None
    legacy: bytes | None
    failure: str | None = None

    def confirms(self, other: ManifestSnapshot) -> bool:
        """Whether both reads succeeded and saw exactly the same bytes."""
        return (self.failure is None and other.failure is None
                and self.canonical == other.canonical and self.legacy == other.legacy)


@dataclass(frozen=True)
class LinkClassification:
    verdict: LinkVerdict
    state: WorkspaceManifestState | None = None
    detail: str | None = None
    # The bytes the verdict was read from; ``None`` when no manifest was read.
    snapshot: ManifestSnapshot | None = None


def manifest_snapshot(folder: Path) -> ManifestSnapshot:
    """One read of the bytes of a folder's canonical and legacy manifests, for an exact change check."""
    found: list[bytes | None] = []
    for path in (manifest_path_for(folder), legacy_manifest_path_for(folder)):
        try:
            found.append(path.read_bytes())
        except FileNotFoundError:
            found.append(None)
        except OSError as exc:
            return ManifestSnapshot(None, None, f"{path}: {exc}")
    return ManifestSnapshot(*found)


def _manifest_state_from(folder: Path, snapshot: ManifestSnapshot) -> WorkspaceManifestState:
    """Parse the manifest a snapshot holds, canonical before legacy as ``load_workspace_manifest_state`` does."""
    manifest_path, legacy_path = manifest_path_for(folder), legacy_manifest_path_for(folder)
    for source, rel, content in ((manifest_path, WORKSPACE_MANIFEST_REL, snapshot.canonical),
                                 (legacy_path, WORKSPACE_MANIFEST_LEGACY_REL, snapshot.legacy)):
        if content is None:
            continue
        try:
            # Universal newlines, as ``read_text`` gives the file loader, so a CRLF manifest parses the same.
            text = io.TextIOWrapper(io.BytesIO(content), encoding="utf-8").read()
            data = load_mapping_text(text, source=str(source))
        except (UnicodeDecodeError, YamlError) as exc:
            raise WorkspaceBindingError(f"failed to load {rel}: {exc}") from exc
        return WorkspaceManifestState(folder, manifest_path, legacy_path, source, data)
    return WorkspaceManifestState(folder, manifest_path, legacy_path, None, None)


def classify_link(vault_root: Path, folder: Path, key: str) -> LinkClassification:
    """Whether the manifest at ``folder`` is the workspace end of the Brain's row ``key``.

    A read only: it takes no lock and writes nothing, and the verdict comes from
    the bytes in ``snapshot``, so a caller can confirm a later read saw the same
    manifest. Absence proves nothing: a ``brain`` ID that does not resolve on
    this machine is ``BRAIN_UNRESOLVED``, never ``OTHER_BRAIN``, and a hub key
    that is missing or not a valid key is ``KEY_MISSING`` or ``KEY_INVALID``,
    never ``OTHER_KEY``. Only the two ``OTHER_*`` verdicts are positive
    disagreement.
    """
    try:
        if not folder.is_dir():
            return LinkClassification(LinkVerdict.UNREACHABLE)
        if is_brain_vault(folder):
            return LinkClassification(LinkVerdict.VAULT_ROOT)
    except OSError as exc:
        return LinkClassification(LinkVerdict.UNREADABLE, detail=str(exc))
    snapshot = manifest_snapshot(folder)
    if snapshot.failure is not None:
        return LinkClassification(LinkVerdict.UNREADABLE, detail=snapshot.failure, snapshot=snapshot)
    try:
        state = _manifest_state_from(folder, snapshot)
    except WorkspaceBindingError as exc:
        return LinkClassification(LinkVerdict.UNREADABLE, detail=str(exc), snapshot=snapshot)
    if state.data is None:
        return LinkClassification(LinkVerdict.NO_MANIFEST, state, snapshot=snapshot)
    brain, linked_key = link_fields(state.data)
    if not isinstance(brain, str) or not brain:
        return LinkClassification(LinkVerdict.BRAIN_UNRESOLVED, state, snapshot=snapshot)
    try:
        bound = resolve_local_brain_vault(brain)
    except WorkspaceBindingError as exc:
        return LinkClassification(LinkVerdict.BRAIN_UNRESOLVED, state, str(exc), snapshot)
    if bound is None:
        return LinkClassification(LinkVerdict.BRAIN_UNRESOLVED, state, snapshot=snapshot)
    if bound != Path(vault_root).resolve():
        return LinkClassification(LinkVerdict.OTHER_BRAIN, state, snapshot=snapshot)
    if linked_key is None:
        return LinkClassification(LinkVerdict.KEY_MISSING, state, snapshot=snapshot)
    if not is_valid_key(linked_key):
        return LinkClassification(LinkVerdict.KEY_INVALID, state, snapshot=snapshot)
    if linked_key != key:
        return LinkClassification(LinkVerdict.OTHER_KEY, state, snapshot=snapshot)
    return LinkClassification(LinkVerdict.MATCHES, state, snapshot=snapshot)


def describe_link(classification: LinkClassification) -> str | None:
    """Why the folder is not the link's workspace end, in words, or ``None`` when it is."""
    verdict = classification.verdict
    if verdict is LinkVerdict.MATCHES:
        return None
    if verdict is LinkVerdict.UNREADABLE:
        return f"the folder could not be inspected ({classification.detail})"
    return {
        LinkVerdict.UNREACHABLE: "the folder is unreachable",
        LinkVerdict.VAULT_ROOT: "the folder is a Brain vault root",
        LinkVerdict.NO_MANIFEST: "the folder has no workspace manifest",
        LinkVerdict.BRAIN_UNRESOLVED: "its manifest's Brain ID does not resolve on this machine",
        LinkVerdict.KEY_MISSING: "its manifest names no workspace key",
        LinkVerdict.KEY_INVALID: "its manifest's workspace key is not a valid key",
        LinkVerdict.OTHER_BRAIN: "its manifest names another Brain",
        LinkVerdict.OTHER_KEY: "its manifest names another workspace",
    }[verdict]


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
