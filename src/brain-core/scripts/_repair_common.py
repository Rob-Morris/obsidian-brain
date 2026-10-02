#!/usr/bin/env python3
"""The Brain repair table and the guidance derived from it (DD-082).

One ``RepairFamily`` type, one table per owner: ``REPAIR_SCOPES`` is the Brain
table, read by ``vault.check``, Doctor, ``repair.py`` and the maintenance
pass. The machine table lives beside the machine pass in the launcher.

Bootstrap tier: stdlib plus ``_common`` and ``_bootstrap`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Mapping

from _bootstrap.maintenance_findings import Disposition, Owner
from _bootstrap.runtime import (
    DEFAULT_MANAGED_RUNTIME_LAUNCHER,
    find_launcher_python,
)
from _common import join_argv


__all__ = [
    "AUTOMATIC_SCOPES", "Disposition", "JUDGEMENT_CODES", "NEVER_HELD", "Owner", "RECOVERY_SCOPES",
    "REPAIR_SCOPES", "RepairFamily", "attach_repair_guidance", "build_catalogue_argv",
    "build_catalogue_command", "build_repair_argv", "build_repair_command", "build_repair_metadata",
    "family_for_finding", "find_launcher_binary",
]

REPAIR_SCRIPT_REL = Path(".brain-core/scripts/repair.py")
COMMAND_SCRIPT_REL = Path(".brain-core/scripts/command.py")


@dataclass(frozen=True, slots=True)
class RepairFamily:
    """One repair scope: the command that repairs it and how a pass treats it.

    ``request`` is the static request payload for ``command_id``;
    ``recovery`` marks a ``repair.py`` bootstrap-recovery scope (DD-043);
    ``exceptional`` marks a command whose initial authorisation class refuses
    standalone calls, so guidance names the ``brain session run`` form;
    ``holdable`` is false for a family detection depends on, which a claim
    never withholds from the pass; ``clears_embeddings`` marks a cache repair
    that degrades semantic retrieval until the semantic repair runs. The
    single-source repair-table test checks the catalogue-facing flags.
    """

    scope: str
    command_id: str
    request: Mapping[str, object]
    disposition: Disposition
    owner: Owner
    recovery: bool
    description: str
    exceptional: bool = False
    holdable: bool = True
    clears_embeddings: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", MappingProxyType(dict(self.request)))
        if not self.scope.strip():
            raise ValueError("repair family requires a scope")
        if not self.command_id or "." not in self.command_id:
            raise ValueError("repair family requires a noun.verb command identifier")
        if not isinstance(self.disposition, Disposition) or not isinstance(self.owner, Owner):
            raise ValueError("repair family disposition and owner must be typed")
        if self.exceptional and self.disposition is Disposition.AUTOMATIC:
            raise ValueError("an exceptional repair can never be automatic")
        if not self.description.strip():
            raise ValueError("repair family requires a description")

    @property
    def noun(self) -> str:
        return self.command_id.split(".", 1)[0]

    @property
    def verb(self) -> str:
        return self.command_id.split(".", 1)[1]


def _brain(scope, command_id, request, disposition, description, *, recovery=True, exceptional=False,
           holdable=True, clears_embeddings=False):
    return RepairFamily(scope, command_id, request, disposition, Owner.BRAIN, recovery, description,
                        exceptional=exceptional, holdable=holdable, clears_embeddings=clears_embeddings)


def _machine(scope, command_id, description):
    return RepairFamily(scope, command_id, {}, Disposition.JUDGEMENT, Owner.MACHINE, True, description)


def _table(*families: RepairFamily) -> Mapping[str, RepairFamily]:
    return MappingProxyType({family.scope: family for family in families})


REPAIR_SCOPES: Mapping[str, RepairFamily] = _table(
    _brain(
        "router", "runtime.refresh-router", {}, Disposition.AUTOMATIC,
        "Rebuild the compiled router cache.",
        holdable=False, clears_embeddings=True,
    ),
    _brain(
        "lexical", "retrieval.refresh-lexical", {}, Disposition.AUTOMATIC,
        "Rebuild the lexical retrieval index cache.",
        clears_embeddings=True,
    ),
    _brain(
        "temporaries", "runtime.remove-temporaries", {}, Disposition.AUTOMATIC,
        "Remove stranded atomic-write temporary files from .brain/local.",
        recovery=False,
    ),
    _brain(
        "frontmatter", "artefact.repair", {"scope": "frontmatter"}, Disposition.JUDGEMENT,
        "Repair duplicate artefact frontmatter blocks by merging nested frontmatter into the document frontmatter.",
    ),
    _brain(
        "ownership", "artefact.repair", {"scope": "ownership"}, Disposition.JUDGEMENT,
        "Reconcile derived owner folders and paths towards valid authoritative parent metadata.",
    ),
    _brain(
        "empty_folders", "artefact.repair", {"scope": "empty_folders"}, Disposition.JUDGEMENT,
        "Remove vacated-empty artefact folders (junk-only contents) under type roots and _Archive.",
    ),
    _brain(
        "semantic", "retrieval.repair-semantic", {}, Disposition.JUDGEMENT,
        "Repair semantic runtime provisioning and embeddings sidecars for this vault.",
        exceptional=True,
    ),
    _brain(
        "registry", "workspace.repair-registry", {}, Disposition.AUTOMATIC,
        "Rebuild a malformed linked workspace registry without its invalid rows and drop rows whose manifest names another Brain or workspace.",
    ),
    _machine(
        "runtime", "runtime.repair",
        "Repair the central managed Brain runtime and its baseline packages via bootstrap, then verify the result.",
    ),
    _machine(
        "mcp", "mcp.repair",
        "Repair current-vault Claude/Codex MCP registration state against a usable managed runtime.",
    ),
)

# Judgement findings that have no repair family: (check, code) pairs whose
# findings are claimable and dismissible per file.
JUDGEMENT_CODES = frozenset({
    ("workspace_contract", "workspace_reference_missing"),
    ("workspace_contract", "workspace_reference_archived"),
    ("workspace_registry", "workspace_link_unverifiable"),
    ("workspace_registry", "workspace_folder_unreachable"),
    ("workspace_registry", "workspace_links_unverified"),
    ("workspace_registry", "workspace_registry_unreadable"),
    ("workspace_registry", "workspace_registry_unparseable"),
})

RECOVERY_SCOPES = tuple(scope for scope, family in REPAIR_SCOPES.items() if family.recovery)
AUTOMATIC_SCOPES = tuple(
    scope for scope, family in REPAIR_SCOPES.items()
    if family.disposition is Disposition.AUTOMATIC
)
NEVER_HELD = frozenset(scope for scope, family in REPAIR_SCOPES.items() if not family.holdable)


@lru_cache(maxsize=1)
def find_launcher_binary() -> str | None:
    """Locate the machine ``brain`` launcher on PATH once per process; patched by tests."""
    return shutil.which("brain")


def build_repair_argv(
    vault_root: str | Path,
    scope: str,
    *,
    launcher: str | None = None,
    json_mode: bool = False,
    dry_run: bool = False,
) -> list[str]:
    """Return argv for the exact repair.py invocation for one recovery scope."""
    vault_root = Path(vault_root).resolve()
    script_path = vault_root / REPAIR_SCRIPT_REL
    launcher = launcher or find_launcher_python() or DEFAULT_MANAGED_RUNTIME_LAUNCHER
    argv = [
        launcher,
        str(script_path),
        scope,
        "--vault",
        str(vault_root),
    ]
    if dry_run:
        argv.append("--dry-run")
    if json_mode:
        argv.append("--json")
    return argv


def build_repair_command(
    vault_root: str | Path,
    scope: str,
    *,
    launcher: str | None = None,
    json_mode: bool = False,
    dry_run: bool = False,
) -> str:
    """Return an exact shell-ready repair.py command for one recovery scope."""
    return join_argv(
        build_repair_argv(
            vault_root,
            scope,
            launcher=launcher,
            json_mode=json_mode,
            dry_run=dry_run,
        )
    )


def _request_argv(family: RepairFamily) -> list[str]:
    if not family.request:
        return []
    return ["--request-json", json.dumps(dict(family.request), separators=(",", ":"), sort_keys=True)]


def build_catalogue_argv(vault_root: str | Path, family: RepairFamily) -> list[str]:
    """Return argv naming the catalogue command that repairs ``family``.

    Three forms: a Brain-owned, initially authorised family runs through the
    launcher when one is on PATH, else through the vault's own ``command.py``;
    an exceptional Brain-owned family needs a ``brain session run`` job; a
    machine-owned family runs through the launcher, else through the
    ``repair.py`` recovery scope, because ``command.py`` dispatches
    application commands only.
    """
    root = Path(vault_root).resolve()
    launcher = find_launcher_binary()
    if family.owner is Owner.MACHINE:
        if launcher is not None:
            return [launcher, "--vault", str(root), family.noun, family.verb, *_request_argv(family)]
        return build_repair_argv(root, family.scope)
    if family.exceptional:
        binary = launcher or "brain"
        return [binary, "--vault", str(root), "session", "run", "--",
                binary, "--vault", str(root), family.noun, family.verb, *_request_argv(family)]
    if launcher is not None:
        return [launcher, "--vault", str(root), family.noun, family.verb, *_request_argv(family)]
    python = find_launcher_python() or DEFAULT_MANAGED_RUNTIME_LAUNCHER
    return [python, str(root / COMMAND_SCRIPT_REL), family.noun, family.verb,
            "--vault", str(root), *_request_argv(family)]


def build_catalogue_command(vault_root: str | Path, family: RepairFamily) -> str:
    """Return the shell-ready catalogue command that repairs ``family``."""
    return join_argv(build_catalogue_argv(vault_root, family))


def build_repair_metadata(vault_root: str | Path, scope: str) -> dict:
    """Return structured repair guidance for a compliance finding."""
    family = REPAIR_SCOPES[scope]
    return {
        "scope": family.scope,
        "description": family.description,
        "command_id": family.command_id,
        "command": build_catalogue_command(vault_root, family),
    }


def family_for_finding(finding: Mapping) -> RepairFamily | None:
    """The repair family that owns a finding, or ``None`` when it carries no ``repair``.

    Producers attach ``repair`` only through ``attach_repair_guidance``, so a
    ``repair`` that is not a mapping, or names a scope this table lacks, is a
    broken producer and fails loudly: ``ValueError`` and ``KeyError``.
    """
    if "repair" not in finding:
        return None
    repair = finding["repair"]
    if not isinstance(repair, Mapping):
        raise ValueError(f"finding {finding.get('check')!r} carries a non-mapping repair: {repair!r}")
    return REPAIR_SCOPES[repair["scope"]]


def attach_repair_guidance(finding: dict, vault_root: str | Path, scope: str) -> dict:
    """Attach structured + human repair guidance to a finding dict."""
    metadata = build_repair_metadata(vault_root, scope)
    finding["repair"] = metadata
    finding.setdefault("fix", f"Run `{metadata['command']}`")
    return finding
