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
    Identity,
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
    warning or info finding is one only when ``JUDGEMENT_FINDINGS`` lists it.
    Per-file judgement findings are keyed by ``check:code``, or by ``check``
    for a check that declares no code, and carry the identity their table
    row promises (DD-086). A finding whose shape breaks that promise, or an
    error no row classifies, is demoted to kind-only, so it can never be
    quieted, and names the breach for the result's warnings; detection
    itself goes on, so unrelated families and automatic repairs proceed.
    """
    from _repair_common import JUDGEMENT_FINDINGS, family_for_finding

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
        elif raw["severity"] == "error" or (check, code) in JUDGEMENT_FINDINGS:
            subject = {"file": file}
            kind = f"{check}:{code}" if code else check
            identity, breach = _promised_identity(kind, JUDGEMENT_FINDINGS.get((check, code)), subject, evidence)
            findings.append(MaintenanceFinding(
                check, raw["severity"], file, raw["message"], Disposition.JUDGEMENT, code=code,
                owner=Owner.BRAIN, key=finding_key(NAMESPACE, kind, subject),
                subject=subject, evidence=evidence, identity=identity, breach=breach,
            ))
        else:
            findings.append(MaintenanceFinding(
                check, raw["severity"], file, raw["message"], Disposition.REPORT_ONLY, code=code, evidence=evidence,
            ))
    return tuple(findings)


def _promised_identity(kind: str, row: Identity | None, subject: Mapping[str, object],
                       evidence: Mapping[str, object] | None) -> tuple[Identity, str | None]:
    """The identity a dismissal of ``kind`` may rely on: its row's, or kind-only with the breach named."""
    if row is None:
        return Identity.KIND_ONLY, f"{kind} is an error with no repair family that JUDGEMENT_FINDINGS does not classify"
    if row.admits(subject, evidence):
        return row, None
    declared = ("a file" if any(value is not None for value in subject.values()) else "no file") + " and " + (
        "no evidence" if evidence is None else "empty evidence" if not evidence else "evidence")
    return Identity.KIND_ONLY, f"{kind} is classified as {row.value} but declares {declared}"


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
