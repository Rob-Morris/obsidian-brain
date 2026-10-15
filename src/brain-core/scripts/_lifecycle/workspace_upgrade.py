"""Read-only semantic admission for a target Core workspace upgrade."""

from pathlib import Path

from _common import normalize_artefact_key

from .workspace_checks import workspace_findings
from .ownership_graph import read_ownership_graph


FINDING_LIMIT = 20
ROOT_WORKSPACE_VERSION = (0, 73, 0)


def preflight_workspace_upgrade(vault_root, *, installed_version=None) -> dict:
    """Check workspace semantics without persisting a router or repairing content.

    The caller loads this module from the target Core before committing any
    Core writes. Folder metadata conversion remains a separate upgrade phase.
    """
    if installed_version is not None and installed_version >= ROOT_WORKSPACE_VERSION:
        return {"outcome": "ready", "findings": [], "total_count": 0,
                "message": "The installed Core already provides the workspace ownership contract; no compatibility migration is required."}
    root = Path(vault_root).resolve()
    fields_by_path = {}
    identities = {}
    try:
        graph = read_ownership_graph(root)
        fields_by_path = {record.path: record.fields for record in graph.records}
        identities = graph.identities
        relevant = any(
            record.fields.get("type") == "living/workspace"
            or record.fields.get("workspace") is not None
            or (record.parent or "").startswith("workspace/")
            for record in graph.records)
        if relevant:
            import compile_router

            router = compile_router.compile(str(root))
            observed = workspace_findings(root, router, graph=graph)
        else:
            observed = []
    except (OSError, UnicodeError, ValueError) as exc:
        observed = [{"code": "workspace_scan_unreadable", "severity": "error",
                     "file": None, "message": str(exc),
                     "fix": "Review the unreadable source explicitly, then retry the read-only vault check"}]

    def blocks(finding):
        if finding["severity"] != "error":
            return False
        if finding["code"] != "workspace_ownership_invalid":
            return True
        fields = fields_by_path.get(finding["file"], {})
        cause = finding.get("evidence", {}).get("cause")
        if (fields.get("type") == "living/workspace"
                or fields.get("workspace") is not None
                or cause in {"workspace_parent", "workspace_mismatch"}):
            return True
        parent = normalize_artefact_key(fields.get("parent"))
        return any(owner.fields.get("type") == "living/workspace"
                   or owner.fields.get("workspace") is not None
                   for owner in identities.get(parent, ()))

    findings = [finding for finding in observed if blocks(finding)]
    blocked = bool(findings)
    bounded = [{"code": row["code"], "file": (row.get("file") or "vault")[:400],
                "message": row["message"][:900], "fix": row.get("fix", "")[:700],
                "evidence": {"cause": row.get("evidence", {}).get("cause")}}
               for row in findings[:FINDING_LIMIT]]
    affected = "; ".join(f"{row['file']}: {row['message']}" for row in bounded[:8])
    if len(findings) > 8:
        affected += (f"; {len(findings) - 8} further findings are omitted; "
                     "review the reported records and rerun the upgrade after the approved repairs.")
    return {
        "outcome": "blocked" if blocked else "ready",
        "findings": bounded,
        "total_count": len(findings),
        "message": (
            "Target Core workspace compatibility blocked: " + affected + ". "
            "Ownership repairs require explicit approval; "
            "use artefact.reparent, artefact.set-workspace or workspace.update-policy with "
            "reviewed canonical identities and arguments, then rerun this preflight. "
            if blocked else "Workspace semantics are ready for upgrade. "
        ) + "This read-only preflight changed no content. Folder-format conversion is a separate phase; "
            "it does not repair ownership or rename identities.",
        "read_only_check": {
            "command": "vault.check",
            "arguments": {"check": "workspace_contract"},
            "argv": ["brain", "--vault", str(root), "vault", "check",
                     "--request-json", '{"check":"workspace_contract"}'],
        },
    }
