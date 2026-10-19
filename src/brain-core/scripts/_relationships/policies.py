"""Explicit scoped consumers of generic assertions, without executable rules."""

from dataclasses import dataclass, field, asdict
import json

from .model import Endpoint, node_key, digest, canonical


@dataclass(frozen=True, slots=True)
class AssessmentPolicy:
    subject: Endpoint = field(metadata={"description": "Exact artefact or assertion assessed by this explicitly selected policy."})
    criterion: Endpoint = field(metadata={"description": "Resolvable criterion whose persisted revision must be pinned by each claim."})
    scope: str = field(metadata={"description": "Explicit claim scope; independent scopes and dimensions are never merged."})
    claim_predicate: str = field(default="assesses", metadata={"description": "Registered predicate connecting the reviewer subject to the assessed object."})
    criterion_predicate: str = field(default="criterion", metadata={"description": "Registered claim-to-criterion predicate."})
    verdict_predicate: str = field(default="verdict", metadata={"description": "Registered claim-to-literal verdict predicate."})
    evidence_predicate: str = field(default="evidence", metadata={"description": "Registered claim-to-evidence predicate."})
    attribution_predicate: str = field(default="attributed-to", metadata={"description": "Registered attribution predicate; attribution does not authenticate a reviewer."})
    subject_revision_predicate: str = field(default="subject-revision", metadata={"description": "Literal exact subject revision pin predicate."})
    criterion_revision_predicate: str = field(default="criterion-revision", metadata={"description": "Literal exact criterion revision pin predicate."})
    evidence_revision_predicate: str = field(default="evidence-revision", metadata={"description": "Literal exact revision pin on each evidence assertion."})
    definition_revisions_predicate: str = field(default="definition-revisions", metadata={"description": "Literal JSON object pinning every policy predicate and the assessed assertion's own predicate semantic contract revision."})
    accepted_value: str = field(default="accepted", metadata={"description": "Explicit accepted verdict; other current verdicts remain adverse/conflicting, never latest-wins."})


@dataclass(frozen=True, slots=True)
class TranscriptPolicy:
    sources: tuple[Endpoint, ...] = field(metadata={"description": "Explicit selected persisted transcript sources, including temporal and multiple sources."})
    transcripts: tuple[Endpoint, ...] = field(metadata={"description": "Explicit selected persisted transcripts; empty is a missing obligation only under this policy."})
    scope: str = field(metadata={"description": "Confirmed workflow scope; native ready/implemented status never implies this obligation."})
    source_predicate: str = field(default="source", metadata={"description": "Registered transcript-to-source predicate."})
    transcripts_predicate: str = field(default="transcripts", metadata={"description": "Registered source-to-transcript predicate."})


def revision_for(node, resolver, records):
    resolved = resolver.resolve(node)
    if resolved["kind"] == "artefact":
        from .endpoints import binding_id
        binding = resolver.store.read("endpoint", binding_id(resolved["reference"])) or resolver.bindings[binding_id(resolved["reference"])]
        if binding["path"].startswith("_Archive/"):
            raise ValueError("archived evidence or subject is not current for this policy")
        return resolver.observations[binding["path"]]
    if resolved["kind"] == "predicate":
        from .definitions import definition
        return definition(resolver.store, "predicate", resolved["reference"])["contract_revision"]
    kind = {"relationship": "assertion", "occurrence": "occurrence", "event": "event"}[resolved["kind"]]
    return records[(kind, resolved["reference"])]["revision"]


def assess(policy, records, resolver, *, work):
    """All qualifying claims participate; stale inputs cannot establish standing."""
    from .definitions import definition
    policy_revision = digest(asdict(policy))
    assertions = [record for (kind, _), record in records.items() if kind == "assertion" and not record.get("retired")]
    subject = resolver.resolve(asdict(policy.subject))
    criterion = resolver.resolve(asdict(policy.criterion))
    subject_revision = revision_for(subject, resolver, records)
    criterion_revision = revision_for(criterion, resolver, records)
    predicates = {value for key, value in asdict(policy).items() if key.endswith("_predicate")}
    if subject["kind"] == "relationship":
        predicates.add(records[("assertion", subject["reference"])]["predicate"])
    contracts = {key: definition(resolver.store, "predicate", key)["contract_revision"] for key in predicates}
    claims = [row for row in assertions if row["predicate"] == policy.claim_predicate
              and node_key(row["object"]) == node_key(subject) and row["scope"] == policy.scope]
    outcomes = []
    adjacency = {}
    for row in assertions:
        work.take()
        adjacency.setdefault((node_key(row["subject"]), row["predicate"], row["scope"]), []).append(row)
    def attached(identity, predicate):
        work.take()
        return adjacency.get(("relationship:" + identity, predicate, policy.scope), [])
    def literal(identity, predicate):
        values = attached(identity, predicate)
        if len(values) != 1 or values[0]["object"]["kind"] != "literal":
            raise ValueError("expected one explicit literal pin/value: " + predicate)
        return values[0]["object"].get("value")
    for claim in claims:
        try:
            resolver.resolve(claim["subject"])
            criterion_edges = attached(claim["id"], policy.criterion_predicate)
            if len(criterion_edges) != 1 or node_key(resolver.resolve(criterion_edges[0]["object"])) != node_key(criterion):
                raise ValueError("criterion is absent, ambiguous or out of scope")
            if literal(claim["id"], policy.subject_revision_predicate) != subject_revision:
                raise ValueError("subject revision changed")
            if literal(claim["id"], policy.criterion_revision_predicate) != criterion_revision:
                raise ValueError("criterion revision changed")
            pinned = json.loads(literal(claim["id"], policy.definition_revisions_predicate))
            if pinned != contracts:
                raise ValueError("semantic definition contract revisions changed")
            attributions = attached(claim["id"], policy.attribution_predicate)
            if not attributions:
                raise ValueError("attribution missing")
            for attribution in attributions:
                resolver.resolve(attribution["object"])
            evidence = attached(claim["id"], policy.evidence_predicate)
            if not evidence:
                raise ValueError("qualifying evidence missing")
            for edge in evidence:
                actual = revision_for(edge["object"], resolver, records)
                if literal(edge["id"], policy.evidence_revision_predicate) != actual:
                    raise ValueError("evidence revision changed")
            verdict = literal(claim["id"], policy.verdict_predicate)
            outcomes.append({"identity": claim["id"], "state": "current", "verdict": verdict})
        except (ValueError, KeyError, OSError) as exc:
            outcomes.append({"identity": claim["id"], "state": "stale", "reason": str(exc)})
    verdicts = {canonical(row["verdict"]) for row in outcomes if row["state"] == "current"}
    state = ("missing" if not outcomes else "unknown" if any(row["state"] != "current" for row in outcomes)
             else "conflicting" if len(verdicts) > 1 else "satisfied" if verdicts == {canonical(policy.accepted_value)} else "adverse")
    return {"policy": "assessment", "policy_revision": policy_revision, "scope": policy.scope,
            "state": state, "claims": outcomes, "attribution_is_authenticated": False}


def transcript_reciprocity(policy, records, resolver, *, work):
    sources = [resolver.resolve(asdict(node)) for node in policy.sources]
    transcripts = [resolver.resolve(asdict(node)) for node in policy.transcripts]
    assertions = [row for (kind, _), row in records.items() if kind == "assertion" and not row.get("retired") and row["scope"] == policy.scope]
    work.take(len(assertions) + len(sources) + len(transcripts))
    triples = {(node_key(row["subject"]), row["predicate"], node_key(row["object"])) for row in assertions}
    missing = []
    for source in sources:
        for transcript in transcripts:
            for subject, predicate, obj in ((source, policy.transcripts_predicate, transcript), (transcript, policy.source_predicate, source)):
                work.take()
                if (node_key(subject), predicate, node_key(obj)) not in triples:
                    missing.append({"subject": subject, "predicate": predicate, "object": obj})
    return {"policy": "transcript-reciprocity", "policy_revision": digest(asdict(policy)), "scope": policy.scope,
            "state": "satisfied" if sources and transcripts and not missing else "missing", "missing": missing}
