"""Three-way reconciliation of opted-in native approval items (no filesystem I/O)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReconciledItems:
    observed: dict
    owned: dict
    exclusions: tuple[str, ...]
    findings: tuple[tuple[str, str], ...]


def reconcile_items(desired: dict, observed: dict, owned: dict, exclusions: tuple[str, ...] = (),
                    *, adopt: tuple[str, ...] = (), restore: tuple[str, ...] = (),
                    remove: bool = False) -> ReconciledItems:
    """Preserve user edits/deletions and unowned equality, including on removal."""
    output, receipts = deepcopy(observed), deepcopy(owned)
    excluded = set(exclusions)
    findings = []
    for key, receipt in receipts.items():
        if not isinstance(receipt, dict) or set(receipt) != {"before", "last"} or receipt["last"] is None:
            raise ValueError(f"invalid approval ownership receipt: {key}")
    if set(adopt) - set(desired) or set(restore) - set(desired):
        raise ValueError("adopt/restore must identify exact current desired approval items")
    if remove and (adopt or restore):
        raise ValueError("approval removal cannot adopt or restore items")
    if remove:
        desired = {}
    for key in sorted(set(desired) | set(receipts)):
        current = observed.get(key)
        wanted = desired.get(key)
        receipt = receipts.get(key)
        if key in adopt and current is not None and current == wanted:
            excluded.discard(key)
            receipts[key] = {"before": deepcopy(current), "last": deepcopy(wanted)}
            continue
        if key in restore and current is None:
            excluded.discard(key)
            receipts.pop(key, None)
            receipt = None
        if receipt is not None and current != receipt["last"]:
            findings.append((key, "deleted" if current is None else "modified"))
            excluded.add(key)
            # Keep evidence until explicit restoration/detach; never silently re-adopt.
            continue
        if receipt is not None:
            if wanted is None:
                if receipt["before"] is None:
                    output.pop(key, None)
                else:
                    output[key] = deepcopy(receipt["before"])
                del receipts[key]
            else:
                output[key] = deepcopy(wanted)
                receipt["last"] = deepcopy(wanted)
            continue
        if key in excluded:
            findings.append((key, "excluded"))
        elif current is not None:
            if key in adopt and current == wanted:
                receipts[key] = {"before": deepcopy(current), "last": deepcopy(wanted)}
            else:
                findings.append((key, "unowned_matching" if current == wanted else "unowned_conflict"))
        elif wanted is not None:
            output[key] = deepcopy(wanted)
            receipts[key] = {"before": None, "last": deepcopy(wanted)}
    return ReconciledItems(output, receipts, tuple(sorted(excluded)), tuple(findings))
