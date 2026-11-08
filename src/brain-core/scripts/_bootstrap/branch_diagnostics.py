"""Read-only branch findings shared by check and Doctor; never automatic repair."""

from .branch_guard import inspect_guard


def branch_guard_findings(vault_root):
    state = inspect_guard(vault_root)
    status = state["status"]
    if status in {"unconfigured", "eligible"}:
        return []
    dormant = status == "dormant"
    message = ("Local branch guard is dormant outside Git; its configured branches are retained."
               if dormant else
               f"Protected vault mutations are blocked by the local branch guard ({state['reason'] or status}). "
               f"Observed branch: {state['observed_branch']!r}; allowed branches: {state['allowed_branches']!r}.")
    if dormant and state["reason"] == "invalid-policy":
        message += " Its retained settings are invalid; inspect them before using this vault in Git again."
    details = {
        "file": ".brain/local/config.yaml", "message": message,
        "evidence": {"status": status, "reason": state["reason"],
                     "branch": state["observed_branch"],
                     "allowed_branches": state["allowed_branches"]},
        "fix": "Inspect vault.read-config; select the intended checkout or explicitly configure this vault's branch policy. Repair never changes allowed branches automatically.",
    }
    if dormant:
        return [{"check": "branch_guard", "severity": "info", "code": "dormant", **details}]
    return [{"check": "branch_guard", "severity": "error", "code": "ineligible", **details}]
