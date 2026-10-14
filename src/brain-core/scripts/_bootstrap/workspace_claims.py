"""Observed foreign folder claims over the existing local Brain inventories."""

from dataclasses import dataclass
from pathlib import Path

from .mcp_inventory import local_brains
import vault_registry
import workspace_registry


class WorkspaceClaimConflict(ValueError):
    """A known foreign Brain claims the selected canonical folder."""


@dataclass(frozen=True, slots=True)
class WorkspacePathClaim:
    brain_root: Path
    slug: str
    path: str


@dataclass(frozen=True, slots=True)
class WorkspaceClaimInspection:
    claims: tuple[WorkspacePathClaim, ...]
    observed_files: tuple[Path, ...]
    coverage: tuple[str, ...]

    def require_unclaimed(self):
        if self.claims:
            owners = ", ".join(str(claim.brain_root) for claim in self.claims)
            raise WorkspaceClaimConflict(
                f"Conflicting workspace registration claims for {self.claims[0].path} in Brain {owners}; "
                "remove stale rows explicitly or select their Brain before rebinding")


def inspect_path_claims(plan, selected, folder, *, allowed_roots=(), require_complete=True,
                        allow_unreachable=False):
    """Inspect foreign claims; callers choose whether unknown coverage blocks.

    All reads belong to the supplied FilePlan, so the same evidence can be
    revalidated at admission and each write. Allowed source/target inventories
    remain with their mutation owner, rather than freezing its own row writes.
    MCP tolerates unreachable Brain rows but still refuses malformed inventories
    and foreign read failures; allow_unreachable expresses only that distinction.
    """
    selected = Path(selected).resolve()
    allowed = {selected, *(Path(root).resolve() for root in allowed_roots)}
    path = workspace_registry.canonical_path(folder)
    files = {Path(vault_registry.registry_path())}
    missing, coverage = [], []
    try:
        roots = local_brains(plan, selected,
                            unreachable=missing if allow_unreachable or not require_complete else None)
    except (OSError, ValueError) as exc:
        if require_complete:
            raise
        coverage.append(f"Local Brain inventory could not be fully inspected: {exc}")
        roots = ()
    coverage.extend(str(item) for item in missing)
    claims = []
    for root in roots:
        files.add(root / ".brain-core/VERSION")
        plan.read_bytes(root / ".brain-core/VERSION")
        if root.resolve() in allowed:
            continue
        registry = root / workspace_registry.REGISTRY_REL
        files.add(registry)
        try:
            content = plan.read_bytes(registry)
            rows = {} if content is None else workspace_registry.decode_registry(content)
            if workspace_registry.canonical_path(root) in rows:
                raise ValueError(f"Vault-self is not a workspace registration: {registry}")
        except (OSError, ValueError) as exc:
            if require_complete:
                raise
            coverage.append(f"Could not inspect {registry}: {exc}")
            continue
        if path in rows:
            claims.append(WorkspacePathClaim(root, rows[path]["slug"], path))
    return WorkspaceClaimInspection(tuple(claims), tuple(sorted(files)), tuple(coverage))
