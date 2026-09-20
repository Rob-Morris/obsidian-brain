"""Explicit, bounded migration of literal Codex rules from a mixed default file."""

from __future__ import annotations

from collections import Counter
import hashlib
import json

from _bootstrap.approval_clients import _codex_prefix_shape


def legacy_identity(selection, line):
    return selection.identity + "::legacy:default.rules:" + hashlib.sha256(line.encode()).hexdigest()


def migrate_codex(selection, content, desired, receipts, selected=(), *, remove=False):
    """Move selected exact rules; restore only while their absence remains owned."""
    import ast

    lines = (content or "").splitlines(keepends=True)
    counts = Counter(line.rstrip("\r\n") for line in lines)
    updated = dict(receipts)
    findings = []
    restorations = []
    for identity, receipt in receipts.items():
        if (not isinstance(receipt, dict) or set(receipt) != {"line", "positions"}
                or not isinstance(receipt["line"], str) or not _codex_prefix_shape(selection, receipt["line"])
                or identity != legacy_identity(selection, receipt["line"])
                or not isinstance(receipt["positions"], list) or not receipt["positions"]
                or any(type(i) is not int or i < 0 for i in receipt["positions"])
                or receipt["positions"] != sorted(set(receipt["positions"]))):
            raise ValueError("invalid legacy approval migration receipt")
        if content is None or counts[receipt["line"]]:
            findings.append((identity, "legacy_modified"))
            continue
        if remove:
            restorations.extend((i, receipt["line"] + "\n") for i in receipt["positions"])
            updated.pop(identity)
    if remove:
        for index, line in sorted(restorations):
            lines.insert(min(index, len(lines)), line)
        return "".join(lines), updated, tuple(findings)
    candidates = {}
    for index, line in enumerate((content or "").splitlines()):
        identity = legacy_identity(selection, line)
        if not _codex_prefix_shape(selection, line):
            if "brain" in line.lower() and not line.lstrip().startswith("#"):
                findings.append((identity, "legacy_review_required"))
            continue
        call = ast.parse(line).body[0].value
        fields = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
        canonical = "prefix_rule(pattern=" + json.dumps(fields["pattern"]) + ", decision=" + json.dumps(fields["decision"]) + ")"
        if canonical not in desired:
            findings.append((identity, "legacy_review_required"))
            continue
        candidates.setdefault(identity, {"line": line, "positions": []})["positions"].append(index)
    if set(selected) - candidates.keys():
        raise ValueError("legacy adoption must select exact matching literal rules from inspect")
    for identity, receipt in candidates.items():
        findings.append((identity, "legacy_migrated" if identity in selected else "legacy_matching"))
        if identity in selected:
            if identity in receipts:
                raise ValueError("legacy rule reappeared after migration; detach or resolve its receipt first")
            updated[identity] = receipt
            lines = [line for line in lines if line.rstrip("\r\n") != receipt["line"]]
    return "".join(lines), updated, tuple(findings)
