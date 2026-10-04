"""One in-process detection function shared by every maintenance command (D9).

Detection never goes through a sibling invocation: it has no effects, so
admission adds nothing, and an in-process call works in every context,
including a ``brain session run`` job. ``vault.check`` stays the agent-facing
projection of the same findings and carries no maintenance identity.
"""

from __future__ import annotations

from typing import Mapping

from _bootstrap.maintenance_findings import (
    Disposition,
    MaintenanceFinding,
    Owner,
    family_key,
    finding_key,
)
from ..context import InvocationContext


NAMESPACE = "brain"


def classify(raw_findings) -> tuple[MaintenanceFinding, ...]:
    """Attach disposition, owner, key and evidence to raw ``run_checks`` findings.

    A finding takes its repair family's disposition. Without a family, every
    error is a judgement finding, so none is left only in ``vault.check``; a
    warning or info finding is one only when ``JUDGEMENT_CODES`` lists it.
    Per-file judgement findings are keyed by ``check:code``, or by ``check``
    for a check that declares no code.
    """
    from _repair_common import JUDGEMENT_CODES, family_for_finding

    findings = []
    for raw in raw_findings:
        try:
            family = family_for_finding(raw)
        except KeyError as exc:
            # A broken producer fails detection the same way a malformed repair does.
            raise ValueError(f"{raw.get('check')!r} names an unknown repair scope: {exc}") from exc
        check = raw["check"]
        code = raw.get("code")
        file = raw.get("file")
        evidence = raw.get("evidence")
        if evidence is not None and not isinstance(evidence, Mapping):
            raise ValueError(f"{check} declared non-mapping evidence")
        if family is not None:
            subject = {"scope": family.scope}
            findings.append(MaintenanceFinding(
                check, raw["severity"], file, raw["message"], family.disposition,
                code=code, scope=family.scope, owner=family.owner,
                key=family_key(NAMESPACE, family.scope), subject=subject, evidence=evidence,
            ))
        elif raw["severity"] == "error" or (check, code) in JUDGEMENT_CODES:
            subject = {"file": file}
            kind = f"{check}:{code}" if code else check
            findings.append(MaintenanceFinding(
                check, raw["severity"], file, raw["message"], Disposition.JUDGEMENT, code=code,
                owner=Owner.BRAIN, key=finding_key(NAMESPACE, kind, subject),
                subject=subject, evidence=evidence,
            ))
        else:
            findings.append(MaintenanceFinding(
                check, raw["severity"], file, raw["message"], Disposition.REPORT_ONLY, code=code, evidence=evidence,
            ))
    return tuple(findings)


class DetectionFailed(RuntimeError):
    """``run_checks`` could not read the vault; the same failures ``vault.check`` reports as a conflict."""


def detect(context: InvocationContext) -> tuple[MaintenanceFinding, ...]:
    """Run every check in process and classify the raw findings."""
    import check

    try:
        raw = check.run_checks(context.selected_brain.vault_root, workspace_dir=context.workspace_dir)
        # A broken producer (a non-mapping repair or evidence, an unknown scope) fails detection too.
        return classify(raw["findings"])
    except (OSError, ValueError) as exc:
        raise DetectionFailed(str(exc)) from exc


def semantic_retrieval_configured(vault_root) -> bool:
    """Whether a cache repair that clears embeddings degrades this vault.

    A broken config layer is reported as configured: the honest failure mode
    is one extra ``semantic`` attention item, never a silent omission.
    """
    from _semantic.config import SemanticConfigLoadError, is_semantic_intent_active

    try:
        return bool(is_semantic_intent_active(vault_root))
    except SemanticConfigLoadError:
        return True
