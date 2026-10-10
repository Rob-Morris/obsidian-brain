#!/usr/bin/env python3
"""
vault_registry.py — User-home authoritative Brain registry.

Maps symbolic Brain IDs to simple typed locators. Stored as plain text at
$XDG_CONFIG_HOME/brain/vaults (defaulting to ~/.config/brain/vaults),
one entry per line, tab-separated.

This is a machine-level surface, not a vault-scoped repair.py helper. It owns
user-home registry rows and the default Brain pointer; vault-local repair must
not import it to mutate machine-level state.

Current shipped writer contract is deliberately minimal:

- `local` — `<brain-id>\tlocal\t<absolute-vault-path>`

Legacy two-column entries (`<brain-id>\t<absolute-vault-path>`) are still read
as implicit `local` entries for compatibility. Future non-local kinds may be
preserved opaquely, but this module only resolves/manages local vault paths
today. All per-vault metadata (version, timestamps) lives in each vault's own
`.brain/`.

The machine-wide default Brain ID is stored separately at
$XDG_CONFIG_HOME/brain/default (a single line — the Brain ID). It never
forms part of the vaults row format.

Usage:
    python3 vault_registry.py --register /path/to/vault
    python3 vault_registry.py --register /path/to/vault --id my-brain
    python3 vault_registry.py --unregister /path/to/vault
    python3 vault_registry.py --list [--json]
    python3 vault_registry.py --prune
    python3 vault_registry.py --resolve <brain-id>
    python3 vault_registry.py --set-default <brain-id>
    python3 vault_registry.py --get-default
    python3 vault_registry.py --clear-default
"""

import argparse
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from _common._filesystem import safe_write
from _common._shell import join_argv
from _common._file_lock import exclusive_file_lock
from _common._paths import config_home
from _common._slugs import title_to_slug
from _common._templates import random_short_suffix
from _common._vault import is_brain_vault


TYPE_LOCAL = "local"
TYPE_REMOTE = "remote"
KNOWN_KINDS = frozenset({TYPE_LOCAL, TYPE_REMOTE})
STATUS_RESERVED = "reserved"
STATUS_UNKNOWN_KIND = "unknown-kind"

HEADER = "# brain registry v2 — one Brain per line, <brain-id>\\t<kind>\\t<value>\n"

_BRAIN_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RegistryReadError(RuntimeError):
    """Raised when the authoritative Brain registry exists but cannot be read."""


class RegistryConflictError(ValueError):
    """Raised when a register or set-default call would create a conflict."""


class RegistryPartialApplyError(RuntimeError):
    """Raised when registry rows commit before default-pointer cleanup fails."""

    def __init__(self, message, *, operation, committed_brain_ids):
        super().__init__(message)
        self.operation = operation
        self.committed_brain_ids = tuple(committed_brain_ids)


@dataclass(frozen=True)
class RegistryEntry:
    brain_id: str
    kind: str
    value: str


@dataclass(frozen=True)
class RegistryRegistrationResult:
    brain_id: str
    changed: bool


@dataclass(frozen=True)
class RegistryDefaultResult:
    brain_id: str | None
    changed: bool


@dataclass(frozen=True)
class RegistryRemovalResult:
    removed_brain_ids: tuple[str, ...]
    changed: bool
    default_cleared: bool


def _registry_path():
    return os.fspath(config_home() / "brain" / "vaults")


def _default_path():
    return os.fspath(config_home() / "brain" / "default")


def registry_path():
    """Return the vault registry path (the authoritative list of local Brains) for read-only diagnostics."""
    return _registry_path()


def default_path():
    """Return the authoritative default-pointer path for read-only diagnostics."""
    return _default_path()


@contextlib.contextmanager
def _locked():
    """Serialize load-modify-save across concurrent installers.

    Locks a sibling ``.lock`` file (not the registry itself) so locking works
    before the registry has been created. ``load_registry_entries()`` on its
    own is intentionally unlocked — best-effort reads must not block installer
    prompts on a concurrent writer.
    """
    lock_path = _registry_path() + ".lock"
    with exclusive_file_lock(lock_path):
        yield


def _write_default_unlocked(brain_id):
    """Write the default Brain ID to the default file without locking.

    Must only be called from within an existing _locked() block.
    """
    safe_write(_default_path(), brain_id + "\n")


def _clear_default_unlocked():
    """Remove the default file without locking. Tolerates absence.

    Must only be called from within an existing _locked() block.
    """
    try:
        os.unlink(_default_path())
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RegistryReadError(
            f"could not remove default Brain pointer at {_default_path()}: {exc}"
        ) from exc


def get_default():
    """Return the stored default Brain ID, or None.

    Best-effort unlocked read — mirrors load_registry_entries().
    Missing file or empty content returns None.
    An OS or decoding error is wrapped in RegistryReadError.
    The returned id is returned as-is; staleness classification belongs in
    Phase 2 resolution.
    """
    path = _default_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            brain_id = handle.read().strip()
        return brain_id if brain_id else None
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistryReadError(
            f"could not read default Brain pointer at {path}: {exc}"
        ) from exc


def set_default_action(brain_id, *, dry_run=False):
    """Set or plan the default and report whether the pointer changes."""
    with _locked():
        entries = load_registry_entries()
        entry = entries.get(brain_id)
        if entry is None or entry.kind != TYPE_LOCAL:
            raise RegistryConflictError(
                f"cannot set default: '{brain_id}' is not a registered local Brain; "
                f"register it first"
            )
        if get_default() == brain_id:
            return RegistryDefaultResult(brain_id, False)
        if not dry_run:
            _write_default_unlocked(brain_id)
        return RegistryDefaultResult(brain_id, True)


def set_default(brain_id):
    """Set the machine default Brain ID.

    Validates that brain_id is a known LOCAL entry (raises RegistryConflictError
    otherwise) then atomically writes the default file.  Runs under _locked()
    to keep validation and write atomic.
    """
    set_default_action(brain_id)


def clear_default_action(*, dry_run=False):
    """Remove or plan removal of the default and report whether one exists."""
    with _locked():
        brain_id = get_default()
        if brain_id is None:
            return RegistryDefaultResult(None, False)
        if not dry_run:
            _clear_default_unlocked()
        return RegistryDefaultResult(brain_id, True)


def clear_default():
    """Remove the default Brain pointer.  Tolerates absence."""
    clear_default_action()


def _parse_entry(raw: str) -> RegistryEntry | None:
    fields = [field.strip() for field in raw.split("\t")]
    if len(fields) == 2:
        brain_id, value = fields
        kind = TYPE_LOCAL
    elif len(fields) == 3:
        brain_id, kind, value = fields
    else:
        return None
    if not brain_id or not kind or not value:
        return None
    return RegistryEntry(brain_id=brain_id, kind=kind, value=value)


def load_registry_entries():
    """Load all registry entries keyed by Brain ID.

    Missing file → {}.
    Malformed lines are skipped with a stderr warning.
    Unrecognised kinds are preserved opaquely and warned once per read.
    """
    path = _registry_path()
    result = {}
    malformed = 0
    unknown_kinds: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                entry = _parse_entry(raw)
                if entry is None:
                    malformed += 1
                    continue
                if entry.kind not in KNOWN_KINDS:
                    unknown_kinds.add(entry.kind)
                result[entry.brain_id] = entry
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistryReadError(f"could not read brain registry at {path}: {exc}") from exc
    if malformed:
        print(
            f"vault_registry: skipping {malformed} malformed line(s) in {path}",
            file=sys.stderr,
        )
    if unknown_kinds:
        kinds = ", ".join(sorted(unknown_kinds))
        print(
            f"vault_registry: unrecognised kind(s) in {path}: {kinds}",
            file=sys.stderr,
        )
    return result


def _save_registry_entries(entries):
    """Write typed registry entries atomically."""
    lines = [HEADER]
    for brain_id in sorted(entries):
        entry = entries[brain_id]
        lines.append(f"{brain_id}\t{entry.kind}\t{entry.value}\n")
    safe_write(_registry_path(), "".join(lines))


def _absolute(vault_path):
    """Canonicalize a vault path for registry storage.

    Uses ``realpath`` so paths reaching the same vault via different symlinks
    collapse to one entry — keeps register/unregister idempotent. Note this
    diverges from ``install.sh``'s ``resolve_path`` (which uses ``cd && pwd``
    and doesn't follow symlinks), so the installer may display a different
    path string than what's stored. Cosmetic only.
    """
    path = os.path.expanduser(vault_path)
    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)
    return os.path.realpath(path)


def is_canonical_value(value):
    """Whether a stored local path is in the form Brain registration stores: absolute and its own ``realpath``.

    Registration always stores ``realpath``, so a row that no longer resolves
    to itself (a symlink left or planted at an old path) has drifted. It never
    matches and is stale, so a symlink can never silently move a Brain's identity.
    """
    return os.path.isabs(value) and os.path.realpath(value) == value


def row_matches(entry, vault_path):
    """The one rule for "this local row is the Brain at ``vault_path``": an exact match of the stored
    value against ``realpath(vault_path)``.

    Every reader that maps a path to a Brain ID uses it (registration, the ID
    lookup, unregister, direct-command identity and the CLI cutover). A
    non-canonical row never equals a ``realpath``, so it never matches.
    """
    return entry.kind == TYPE_LOCAL and entry.value == _absolute(vault_path)


STALE_NOT_CANONICAL = "not_canonical"
STALE_NOT_A_BRAIN = "not_a_brain"


def stale_reason(entry):
    """Why a local row is stale (``STALE_NOT_CANONICAL`` or ``STALE_NOT_A_BRAIN``), or ``None``."""
    if not is_canonical_value(entry.value):
        return STALE_NOT_CANONICAL
    if not is_brain_vault(entry.value):
        return STALE_NOT_A_BRAIN
    return None


def alias_owner(entry, entries):
    """The other ID whose canonical row is this drifted row's ``realpath``, or ``None``.

    Old registries can hold that pair: a row whose path became a symlink and a
    second ID registered at the target. The target keeps its own row (and its
    MCP integrations), so the drifted one only needs removing.
    """
    target = os.path.realpath(entry.value)
    return next((other.brain_id for other in _local_entries(entries).values()
                 if other.brain_id != entry.brain_id and other.value == target), None)


MANUAL_RECOVERY = ("This state needs manual recovery: no command recovers it yet, and recovering a moved Brain "
                   "that holds MCP integrations or approvals is a known follow-up.")
APPROVAL_RECOVERY = "Recover managed approvals first; brain approvals inspect --json names the remedy."


@dataclass(frozen=True)
class RemovalRefusal:
    """Why ``brain registry remove-stale`` refuses one stale row, and the remedy for that row."""

    reason: str
    remedy: str


def _removal_refusal(entry, entries):
    """Why ``brain registry remove-stale`` would refuse to remove this stale row, or ``None``.

    ``prune_action`` enforces exactly this rule, so stale guidance never names
    a removal the registry would refuse. A row is never dropped while MCP
    integrations it owns survive: a canonical row's at its path, a drifted
    row's at its ``realpath`` (a file plan refuses to read through the
    symlink), and a duplicate row whose target another ID registers owns none.
    A drifted row also cannot be removed while managed approvals hold records,
    because their inventory refuses every row that is not its own canonical
    path, and no row can be removed while the approval state itself needs
    recovery, because the managed writer then refuses every registry change.
    """
    from _bootstrap import machine_cli

    if machine_cli.approval_state_blocks_changes():
        return RemovalRefusal("the managed client approval state cannot be used (an unreadable approval ledger or "
                              "an interrupted approval transaction)", APPROVAL_RECOVERY)
    target = entry.value
    if not is_canonical_value(entry.value):
        if machine_cli.approval_records_present():
            return RemovalRefusal("managed client approvals are recorded on this machine, and their inventory "
                                  "refuses a row that is not its own canonical path", MANUAL_RECOVERY)
        if alias_owner(entry, entries) is not None:
            return None
        target = os.path.realpath(entry.value)
    try:
        _require_no_mcp_integrations(target)
    except RegistryConflictError as exc:
        return RemovalRefusal(f"the Brain at {target} still has registered MCP integrations, which removing the row "
                              f"would orphan ({exc})", MANUAL_RECOVERY)
    except (OSError, ValueError) as exc:
        # Listing and Doctor report this row as needing manual recovery rather than fail for the machine.
        return RemovalRefusal(f"the MCP state of the Brain at {target} could not be inspected ({exc})", MANUAL_RECOVERY)
    return None


def prune_refusals(entries):
    """Each stale local row that ``brain registry remove-stale`` would refuse, with its ``RemovalRefusal``.

    Remove-stale removes every stale row or none, so one refusal blocks it for
    every row.
    """
    return {brain_id: refusal for brain_id, entry in sorted(_local_entries(entries).items())
            if stale_reason(entry) is not None and (refusal := _removal_refusal(entry, entries)) is not None}


def unregister_guidance(vault_root):
    """The launcher command that unregisters the Brain row stored at ``vault_root``."""
    request = json.dumps({"vault_root": str(vault_root)}, separators=(",", ":"), sort_keys=True)
    return join_argv(["brain", "unregister", "--request-json", request])


def stale_guidance(entry, entries, refusals=None):
    """The one command that recovers a stale row, or ``None`` when it needs manual recovery.

    It names ``brain registry remove-stale`` only when the registry would
    accept it (``prune_refusals`` is empty). While another row blocks it, a
    canonical row that is removable on its own is named for ``brain
    unregister``, which removes it unless managed approvals hold records (their
    strict inventory then refuses the blocking row first).
    """
    from _bootstrap import machine_cli

    refusals = prune_refusals(entries) if refusals is None else refusals
    if not refusals:
        return join_argv(["brain", "registry", "remove-stale"])
    if (entry.brain_id not in refusals and is_canonical_value(entry.value)
            and not machine_cli.approval_records_present()):
        return unregister_guidance(entry.value)
    return None


def _default_id():
    try:
        return get_default()
    except RegistryReadError:
        return None


def stale_explanation(entry, entries, refusals=None):
    """A stale row in words, naming both paths, and how to recover it."""
    refusals = prune_refusals(entries) if refusals is None else refusals
    target = os.path.realpath(entry.value)
    owner = alias_owner(entry, entries)
    if stale_reason(entry) == STALE_NOT_A_BRAIN:
        state = f"Brain ID '{entry.brain_id}' points at {entry.value}, which is not an installed Brain"
    elif owner is not None:
        state = (f"Brain ID '{entry.brain_id}' is stored at {entry.value}, which is no longer its canonical path: "
                 f"it resolves to {target}, which is registered as '{owner}'")
    elif is_brain_vault(target):
        state = (f"Brain ID '{entry.brain_id}' is stored at {entry.value}, which is no longer its canonical path: "
                 f"it resolves to {target}")
    else:
        state = (f"Brain ID '{entry.brain_id}' is stored at {entry.value}, which is no longer its canonical path: "
                 f"it resolves to {target}, which is not an installed Brain")
    own = refusals.get(entry.brain_id)
    if own is not None:
        return f"{state}. brain registry remove-stale refuses it: {own.reason}. {own.remedy}"
    if refusals:
        blocked = f"{state}. brain registry remove-stale is blocked while the stale row '{next(iter(refusals))}' remains"
        guidance = stale_guidance(entry, entries, refusals)
        return f"{blocked}; remove this row on its own with {guidance}" if guidance else blocked
    recovery = f"{state}; run {stale_guidance(entry, entries, refusals)}"
    if owner is not None:
        return f"{recovery} to drop the duplicate row"
    if stale_reason(entry) == STALE_NOT_CANONICAL and is_brain_vault(target):
        recovery = (f"{recovery}, then, if {target} is the Brain formerly at {entry.value}, register it again under "
                    f"the same ID: {register_guidance(target, brain_id=entry.brain_id)}")
        if _default_id() == entry.brain_id:
            # remove-stale clears a default it removes; registering again does not restore it.
            request = json.dumps({"brain_id": entry.brain_id}, separators=(",", ":"), sort_keys=True)
            recovery += ("; it was the default, so make it the default again: "
                         + join_argv(["brain", "set-default", "--request-json", request]))
    return recovery


def _local_entries(entries):
    return {
        brain_id: entry
        for brain_id, entry in entries.items()
        if entry.kind == TYPE_LOCAL
    }


def _find_local_brain_id_by_path(entries, abs_path):
    """Return the local Brain ID mapping to abs_path, or None."""
    for brain_id, entry in _local_entries(entries).items():
        if row_matches(entry, abs_path):
            return brain_id
    return None


def brain_id_for_path(vault_path):
    """Return the Brain ID registered for vault_path, or None when it is unregistered.

    A read: it takes no lock and creates nothing. It applies registration's own
    lookup, an exact match of the stored value against ``_absolute(vault_path)``,
    so it answers "registered" exactly when ``register`` would no-op.
    """
    return _find_local_brain_id_by_path(load_registry_entries(), _absolute(vault_path))


def register_guidance(vault_root, *, brain_id=None):
    """The launcher command that registers the Brain at vault_root, under ``brain_id`` when given."""
    payload = {"vault_root": str(vault_root)}
    if brain_id is not None:
        payload["brain_id"] = brain_id
    request = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return join_argv(["brain", "register", "--request-json", request])


def _is_valid_brain_id(brain_id):
    """Return True when brain_id is a valid slug (lowercase alphanumeric and hyphens)."""
    return bool(brain_id and _BRAIN_ID_RE.fullmatch(brain_id))


def _plan_registration(entries, abs_path, brain_id):
    """Return one Brain registration result after mutating only the provided mapping."""
    existing_id = _find_local_brain_id_by_path(entries, abs_path)
    if existing_id is None:
        drifted = sorted(entry.brain_id for entry in _local_entries(entries).values()
                         if not is_canonical_value(entry.value) and os.path.realpath(entry.value) == abs_path)
        if drifted:
            # Never give a Brain whose old row now resolves here a second ID, whatever ID is asked for.
            # A canonical row that already matches (the other half of an old alias pair) still no-ops.
            raise RegistryConflictError(stale_explanation(entries[drifted[0]], entries))

    if brain_id is None:
        if existing_id is not None:
            return RegistryRegistrationResult(existing_id, False)
        base_brain_id = title_to_slug(os.path.basename(abs_path)) or "vault"
        new_id = base_brain_id
        while new_id in entries:
            new_id = f"{base_brain_id}-{random_short_suffix()}"
        entries[new_id] = RegistryEntry(
            brain_id=new_id,
            kind=TYPE_LOCAL,
            value=abs_path,
        )
        return RegistryRegistrationResult(new_id, True)

    existing_entry = entries.get(brain_id)
    if existing_entry is not None:
        if row_matches(existing_entry, abs_path):
            return RegistryRegistrationResult(brain_id, False)
        if existing_entry.kind == TYPE_LOCAL and stale_reason(existing_entry) is not None:
            raise RegistryConflictError(stale_explanation(existing_entry, entries))
        raise RegistryConflictError(
            f"Brain ID '{brain_id}' is already registered to a different path: "
            f"{existing_entry.value!r}; unregister it first"
        )
    if existing_id is not None:
        raise RegistryConflictError(
            f"path {abs_path!r} is already registered as '{existing_id}'; "
            f"unregister it first or pass brain_id='{existing_id}'"
        )
    entries[brain_id] = RegistryEntry(
        brain_id=brain_id,
        kind=TYPE_LOCAL,
        value=abs_path,
    )
    return RegistryRegistrationResult(brain_id, True)


class NotABrainError(ValueError):
    """Brain registration refused: the path is not an installed Brain (no ``.brain-core/VERSION``)."""


def _require_installed_brain(abs_path):
    """Only an installed Brain registers, so a row is never stale on arrival (the narrow predicate)."""
    if not is_brain_vault(abs_path):
        raise NotABrainError(f"{abs_path} is not an installed Brain (no .brain-core/VERSION); only an installed "
                             "local Brain can be registered")


def preview_register_action(vault_path, brain_id=None, *, installing=False):
    """Plan Brain registration without acquiring a lock or creating filesystem state.

    ``installing`` is for an install preview: the path is not a Brain yet, and
    the install that registers it scaffolds ``.brain-core`` first.
    """
    abs_path = _absolute(vault_path)
    if not installing:
        _require_installed_brain(abs_path)
    if brain_id is not None and not _is_valid_brain_id(brain_id):
        raise ValueError(
            f"invalid Brain ID {brain_id!r}: must match ^[a-z0-9]+(-[a-z0-9]+)*$"
        )
    entries = load_registry_entries()
    return _plan_registration(entries, abs_path, brain_id)


def register_action(vault_path, brain_id=None, *, dry_run=False):
    """Register or plan a local vault and report resolved ID/change state.

    When brain_id is None (default):
    - Brain ID = slugified basename.
    - If path already registered (under any Brain ID), returns existing ID (no-op).
    - On basename collision with a different path, appends random [a-z0-9]{3} suffix.

    When brain_id is given:
    - Must be a valid slug (lowercase alphanumeric and hyphens).
    - brain_id maps to THIS path → no-op, return brain_id.
    - brain_id maps to a DIFFERENT path → raises RegistryConflictError.
    - brain_id free, path unregistered → create brain_id → path, return brain_id.
    - brain_id free, path already registered under X → raises RegistryConflictError.
      (Re-keying is not supported; use Phase-3 rename primitives instead.)
    """
    abs_path = _absolute(vault_path)
    _require_installed_brain(abs_path)
    if brain_id is not None and not _is_valid_brain_id(brain_id):
        raise ValueError(
            f"invalid Brain ID {brain_id!r}: must match ^[a-z0-9]+(-[a-z0-9]+)*$"
        )
    with _locked():
        entries = load_registry_entries()
        result = _plan_registration(entries, abs_path, brain_id)
        if result.changed and not dry_run:
            _save_registry_entries(entries)
        return result


def register(vault_path, brain_id=None):
    """Register a local vault. Returns the resolved Brain ID.

    ``register_action`` is the structured owner seam; this function preserves
    the established public scalar return contract.
    """
    return register_action(vault_path, brain_id=brain_id).brain_id


def _require_no_mcp_integrations(vault_path, plan=None):
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.mcp_registration import read_records, McpScope
    from _bootstrap.mcp_inventory import require_owned_native_slots

    plan = plan or FilePlan()
    vault = Path(vault_path)
    _, integrations = read_records(plan, vault, Path.home(), McpScope.PROJECT)
    if integrations:
        raise RegistryConflictError("Remove this Brain's registered MCP integrations before unregistering it; use Brain uninstall for composed removal")
    require_owned_native_slots(plan, vault, Path.home(), ())


def unregister_action(vault_path, *, dry_run=False, registration_plan=None):
    """Remove or plan path-matching entries and report exact state.

    When the removed Brain ID matches the stored default, the default pointer
    is cleared within the same lock. If that second write fails after registry
    rows commit, ``RegistryPartialApplyError`` reports the committed row IDs.
    """
    from _bootstrap.mcp_registration import registration_lock

    if registration_plan is not None and not dry_run:
        raise ValueError("A planned MCP removal can only authorise an unregister preview")
    abs_path = _absolute(vault_path)
    literal = os.path.abspath(os.path.expanduser(os.fspath(vault_path)))
    with registration_lock(Path.home()), _locked():
        entries = load_registry_entries()
        to_remove = [
            brain_id
            for brain_id, entry in _local_entries(entries).items()
            if row_matches(entry, abs_path)
        ]
        to_remove.sort()
        if literal != abs_path:
            # An ordinary spelling through a symlink (/tmp, /var, a symlinked parent) unregisters the Brain it
            # resolves to. It is ambiguous only when a drifted row is involved: the literal path is a drifted
            # row's stored value, or a drifted row also resolves to the same Brain. Then the person may mean
            # that row, never the healthy Brain, so refuse rather than remove the wrong row.
            local = _local_entries(entries).values()
            stored = sorted(entry.brain_id for entry in local if entry.value == literal)
            if stored:
                raise RegistryConflictError(
                    f"{literal} is the stored path of the stale row '{stored[0]}', not a canonical path; "
                    f"{stale_explanation(entries[stored[0]], entries)}")
            drifted = sorted(entry.brain_id for entry in local if not is_canonical_value(entry.value)
                             and os.path.realpath(entry.value) == abs_path)
            if drifted and to_remove:
                raise RegistryConflictError(
                    f"{literal} is not a canonical path: it resolves to {abs_path}, which is registered as "
                    f"'{to_remove[0]}' and is also where the stale row '{drifted[0]}' resolves; pass {abs_path} "
                    f"to unregister '{to_remove[0]}', or recover the stale row: "
                    f"{stale_explanation(entries[drifted[0]], entries)}")
        if not to_remove:
            return RegistryRemovalResult((), False, False)
        _require_no_mcp_integrations(abs_path, registration_plan)
        current_default = get_default()
        default_cleared = current_default is not None and current_default in to_remove
        if dry_run:
            return RegistryRemovalResult(tuple(to_remove), True, default_cleared)
        for brain_id in to_remove:
            del entries[brain_id]
        _save_registry_entries(entries)
        # Clear a dangling default pointer within the same lock (get_default is
        # an unlocked read — safe inside the lock as it never acquires it).
        if default_cleared:
            try:
                _clear_default_unlocked()
            except RegistryReadError as exc:
                raise RegistryPartialApplyError(
                    str(exc),
                    operation="unregister",
                    committed_brain_ids=to_remove,
                ) from exc
        return RegistryRemovalResult(tuple(to_remove), True, default_cleared)


def unregister(vault_path):
    """Remove the local entry keyed to this path. Returns True if removed."""
    return unregister_action(vault_path).changed


class StaleRowError(ValueError):
    """A Brain ID names a stale row: it never selects a Brain (DD-083 item 2)."""


def require_live(brain_id):
    """The canonical path of a local Brain ID's row, or ``None`` when there is no such row.

    A stale row raises ``StaleRowError`` with its recovery, so selecting a Brain
    by ID applies the same rule as matching by path: never follow a drifted row.
    """
    entries = load_registry_entries()
    entry = entries.get(brain_id)
    if entry is None or entry.kind != TYPE_LOCAL:
        return None
    if stale_reason(entry) is not None:
        raise StaleRowError(stale_explanation(entry, entries))
    return entry.value


def resolve(brain_id):
    """Return the absolute local vault path for a Brain ID, or None."""
    entry = load_registry_entries().get(brain_id)
    if entry is None or entry.kind != TYPE_LOCAL:
        return None
    return entry.value


def list_entries():
    """Return sorted registry entries with honest local-vs-non-local detail.

    Each entry dict includes a boolean ``default`` key indicating whether
    it is the current machine default.
    """
    default_id = get_default()
    rendered = []
    entries = load_registry_entries()
    refusals = None
    for brain_id, entry in sorted(entries.items()):
        is_default = brain_id == default_id
        if entry.kind == TYPE_LOCAL:
            reason = stale_reason(entry)
            if reason is not None and refusals is None:
                refusals = prune_refusals(entries)
            rendered.append(
                {
                    "alias": brain_id,
                    "kind": entry.kind,
                    "value": entry.value,
                    "stale": reason is not None,
                    "stale_reason": reason,
                    "stale_guidance": stale_guidance(entry, entries, refusals) if reason else None,
                    "stale_explanation": stale_explanation(entry, entries, refusals) if reason else None,
                    "default": is_default,
                }
            )
            continue
        status = STATUS_RESERVED if entry.kind == TYPE_REMOTE else STATUS_UNKNOWN_KIND
        rendered.append(
            {
                "alias": brain_id,
                "kind": entry.kind,
                "value": entry.value,
                "stale": None,
                "status": status,
                "default": is_default,
            }
        )
    return rendered


def prune_action(*, dry_run=False):
    """Remove or plan stale local entries and report exact state.

    When the stored default points at a pruned Brain ID, the default pointer is
    cleared within the same lock. As with ``unregister_action``, a failure after
    registry rows commit is surfaced as an explicit partial application.
    """
    from _bootstrap.mcp_registration import registration_lock

    with registration_lock(Path.home()), _locked():
        entries = load_registry_entries()
        stale = [
            brain_id
            for brain_id, entry in _local_entries(entries).items()
            if stale_reason(entry) is not None
        ]
        stale.sort()
        if not stale:
            return RegistryRemovalResult((), False, False)
        refusals = prune_refusals(entries)
        if refusals:
            # One rule with the stale guidance: a row that would orphan state refuses, and nothing is removed.
            blocker = next(iter(refusals))
            raise RegistryConflictError(stale_explanation(entries[blocker], entries, refusals))
        current_default = get_default()
        default_cleared = current_default is not None and current_default in stale
        if dry_run:
            return RegistryRemovalResult(tuple(stale), True, default_cleared)
        for brain_id in stale:
            del entries[brain_id]
        _save_registry_entries(entries)
        # Clear a dangling default pointer within the same lock (get_default is
        # an unlocked read — safe inside the lock as it never acquires it).
        if default_cleared:
            try:
                _clear_default_unlocked()
            except RegistryReadError as exc:
                raise RegistryPartialApplyError(
                    str(exc),
                    operation="prune",
                    committed_brain_ids=stale,
                ) from exc
        return RegistryRemovalResult(tuple(stale), True, default_cleared)


def prune():
    """Remove stale local entries. Returns list of removed Brain IDs."""
    return list(prune_action().removed_brain_ids)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="User-home authoritative Brain registry")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", metavar="PATH")
    group.add_argument("--unregister", metavar="PATH")
    group.add_argument("--list", action="store_true")
    group.add_argument("--prune", action="store_true")
    group.add_argument("--resolve", metavar="BRAIN_ID")
    group.add_argument("--set-default", metavar="BRAIN_ID", dest="set_default")
    group.add_argument("--get-default", action="store_true", dest="get_default")
    group.add_argument("--clear-default", action="store_true", dest="clear_default")
    parser.add_argument("--json", action="store_true")
    # --id modifies --register only; not part of the mutually-exclusive group.
    parser.add_argument("--id", metavar="ID", dest="id")
    args = parser.parse_args()

    try:
        from _bootstrap import machine_cli
        if machine_cli.approvals_present() and any((args.register, args.unregister, args.prune, args.set_default, args.clear_default)):
            if args.register:
                command, request = "brain.register", {
                    "vault_root": _absolute(args.register),
                    "brain_id": args.id,
                }
            elif args.unregister:
                # The literal path, not its realpath: the owner refuses a path through a symlink that would
                # remove the wrong row, so it must see the path the person passed.
                command, request = "brain.unregister", {
                    "vault_root": os.path.abspath(os.path.expanduser(args.unregister))}
            elif args.prune:
                command, request = "registry.remove-stale", {}
            elif args.set_default:
                command, request = "brain.set-default", {"brain_id": args.set_default}
            else:
                command, request = "brain.clear-default", {}
            result = machine_cli.invoke(command, request)
            print(json.dumps(result, indent=2))
            raise SystemExit(0 if result["status"] == "ok" else 1)
        if args.register:
            print(register(args.register, brain_id=args.id))
        elif args.unregister:
            unregister(args.unregister)  # best-effort; always exit 0
        elif args.list:
            entries = list_entries()
            if args.json:
                print(json.dumps(entries, indent=2))
            elif not entries:
                print("No Brains registered.")
            else:
                for entry in entries:
                    default_tag = " (default)" if entry.get("default") else ""
                    if entry["kind"] == TYPE_LOCAL:
                        stale_tag = " (stale)" if entry["stale"] else ""
                        print(f"  {entry['alias']} [{entry['kind']}]: {entry['value']}{stale_tag}{default_tag}")
                        continue
                    note = " (reserved; unresolved here)"
                    if entry["status"] == STATUS_UNKNOWN_KIND:
                        note = " (unrecognised kind; unresolved here)"
                    print(f"  {entry['alias']} [{entry['kind']}]: {entry['value']}{note}{default_tag}")
        elif args.prune:
            removed = prune()
            if not removed:
                print("No stale entries.")
            else:
                for brain_id in removed:
                    print(f"Removed: {brain_id}")
        elif args.resolve:
            path = resolve(args.resolve)
            if path is None:
                print(f"Unknown Brain ID: {args.resolve}", file=sys.stderr)
                sys.exit(1)
            print(path)
        elif args.set_default:
            set_default(args.set_default)
        elif args.get_default:
            brain_id = get_default()
            if brain_id is not None:
                print(brain_id)
        elif args.clear_default:
            clear_default()
    except (RegistryReadError, RegistryConflictError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
