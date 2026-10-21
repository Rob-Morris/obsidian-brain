"""Explicit bounded integrity and selected policy evaluation over source bytes."""

from .definitions import definition
from .endpoints import Endpoints, binding_id
from .model import (BudgetExceeded, Work, Conflict, MAX_WORK, assertion_revision, correction_state, digest, event_revision,
                    node_key, slot_key, validate_assertion, validate_definition, validate_event_payload)
from .storage import MAX_RECORD_BYTES, Store, decode


class IncompleteAdjacency(ValueError):
    """A source family could not be inspected completely within this page."""


def inspect(root, router, *, budget=MAX_WORK, assessment=None, transcript=None, identities=(), cursor=None):
    store = Store(root)
    records, revisions, findings, outcomes = {}, {}, [], []
    visited = 0
    work = Work(budget)
    complete = True
    start_directories = store.directory_stamp()
    import base64
    import json
    from dataclasses import asdict
    selectors = digest([identities, asdict(assessment) if assessment else None, asdict(transcript) if transcript else None])
    after = ""
    if cursor:
        try:
            continuation = json.loads(base64.urlsafe_b64decode(cursor))
            if continuation["directories"] != start_directories or continuation["selectors"] != selectors:
                raise Conflict("check continuation changed; restart without cursor")
            after = continuation["after"]
        except (KeyError, TypeError, ValueError) as exc:
            raise Conflict("invalid or changed check continuation; restart without cursor") from exc
    page_limit = max(1, min(100, budget // 8))
    if identities:
        paths = []
        for selector in identities:
            kind, separator, identity = selector.partition(":")
            if not separator:
                raise ValueError("check identity must be storage-kind:id")
            path = store.path("assertion" if kind == "relationship" else kind, identity)
            if str(path.relative_to(store.root)) > after:
                paths.append(path)
        paths = sorted(paths, key=lambda path: str(path.relative_to(store.root)))
    else:
        paths = store.source_page(budget=page_limit, after=after)
    more = len(paths) > page_limit
    for path in paths[:page_limit]:
        if visited >= budget:
            complete = False
            break
        visited += 1
        locator = str(path.relative_to(store.root))
        identity = None
        try:
            work.take()
            raw = path.read_bytes()
            revisions[locator] = digest(raw)
            if len(raw) <= MAX_RECORD_BYTES:
                envelope = json.loads(raw)
                if isinstance(envelope, dict) and path == store.path(envelope.get("kind"), envelope.get("id")):
                    identity = envelope["id"]
            record = decode(raw)
            identity = record["id"]
            if path != store.path(record["kind"], record["id"]):
                raise ValueError("record locator disagrees with identity")
            records[(record["kind"], record["id"])] = record
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            findings.append({"identity": identity, "path": locator, "state": "inaccessible" if isinstance(exc, PermissionError) else "malformed", "reason": str(exc), "remediation": "Repair the record explicitly, then refresh."})
    next_cursor = None
    if more:
        next_cursor = base64.urlsafe_b64encode(json.dumps({"directories": start_directories, "selectors": selectors,
                    "after": str(paths[page_limit-1].relative_to(store.root))}).encode()).decode()
        complete = False
    resolver = Endpoints(store, router, work=work)
    slots = {}
    families = {}

    def source_family(kind):
        if kind in families:
            return families[kind]
        values = {identity: record for (category, identity), record in records.items() if category == kind}
        if more or cursor or identities:
            import heapq
            remaining = budget - work.visited
            paths = heapq.nsmallest(remaining + len(values) + 1, (store.shared / kind).glob("*.json"))
            known_paths = {store.path(kind, identity) for identity in values}
            missing = [path for path in paths if path not in known_paths]
            if len(missing) > remaining:
                raise IncompleteAdjacency("complete source " + kind + " adjacency exceeds the remaining work budget; increase work_budget")
            for path in missing:
                work.take()
                raw = path.read_bytes()
                record = decode(raw)
                if path != store.path(record["kind"], record["id"]) or record["kind"] != kind:
                    raise ValueError("source adjacency locator disagrees with identity")
                values[record["id"]] = record
                revisions[str(path.relative_to(store.root))] = digest(raw)
        families[kind] = values
        return values

    corrections = None
    all_slots = None
    for (kind, identity), record in records.items():
        if record.get("retired"):
            continue
        try:
            work.take()
            if kind in ("predicate", "event-type"):
                if validate_definition(record) != record["contract_revision"]:
                    raise ValueError("invalid semantic contract revision")
                definition(store, kind, identity)
            elif kind == "assertion":
                predicate = definition(store, "predicate", record["predicate"])
                validate_assertion(record, predicate)
                if assertion_revision(record) != record["revision"]:
                    raise ValueError("assertion logical revision does not match meaning")
                for name in ("subject", "object"):
                    resolver.resolve(record[name])
                if predicate.get("cardinality") == "one":
                    if all_slots is None:
                        all_slots = {}
                        for candidate in source_family("assertion").values():
                            if not candidate.get("retired"):
                                all_slots.setdefault(slot_key(candidate), []).append(candidate["id"])
                    slots[slot_key(record)] = all_slots.get(slot_key(record), [])
            elif kind == "occurrence":
                from .native import facts
                from _common import read_exact_file_content, parse_frontmatter
                subject = resolver.resolve(record["subject"])
                binding = store.read("endpoint", binding_id(subject["reference"]))
                fields, _ = parse_frontmatter(read_exact_file_content(store.root / binding["path"]))
                observed = {(predicate, node_key(resolver.resolve(obj))) for predicate, obj in facts(subject["reference"], fields)}
                if (record["predicate"], node_key(record["object"])) not in observed:
                    raise Conflict("native fact changed externally; occurrence and dependants require explicit disposition")
            elif kind == "endpoint":
                resolver.resolve(record["node"])
            elif kind == "event":
                if event_revision(record) != record["revision"]:
                    raise ValueError("event semantic revision does not match retained meaning")
                pinned = store.read("contract", record["contract_revision"].removeprefix("sha256:"))
                if pinned is None:
                    raise ValueError("missing pinned event contract")
                validate_event_payload(record["payload"], pinned["definition"])
                try:
                    validate_event_payload(record["payload"], definition(store, "event-type", record["type"]))
                except ValueError as exc:
                    findings.append({"identity": identity, "state": "current-constraint-mismatch", "reason": str(exc), "definition_revision": record["contract_revision"], "remediation": "Historical validity is preserved; review present-day conformance separately."})
                events = source_family("event")
                if corrections is None:
                    corrections = {}
                    for candidate in events.values():
                        if candidate.get("corrects"):
                            corrections.setdefault(candidate["corrects"], []).append(candidate["id"])
                def successors(key):
                    work.take()
                    source = events.get(key)
                    if source is None or source.get("retired"):
                        return None
                    return corrections.get(key, [])
                interpretation = correction_state(identity, successors, budget=budget)
                if interpretation["state"] in ("unresolved", "incomplete"):
                    raise Conflict(interpretation["reason"])
            elif kind == "operation" and record["state"] not in ("complete", "abandoned"):
                raise Conflict("interrupted semantic publication; inspect receipt then history.repair " + identity)
        except IncompleteAdjacency as exc:
            complete = False
            findings.append({"identity": identity, "state": "not-evaluated", "reason": str(exc),
                             "remediation": "Increase work_budget; this page does not certify cross-page integrity."})
        except BudgetExceeded:
            complete = False
            # Retry this page with a larger budget rather than silently advancing
            # past source records whose semantic validation never ran.
            next_cursor = base64.urlsafe_b64encode(json.dumps({"directories": start_directories,
                          "selectors": selectors, "after": after}).encode()).decode()
            break
        except (ValueError, KeyError, OSError) as exc:
            state = ("inaccessible" if isinstance(exc, PermissionError) else "missing" if isinstance(exc, FileNotFoundError)
                     else "stale" if isinstance(exc, Conflict) else "ambiguous" if "ambiguous" in str(exc).lower() else "malformed")
            findings.append({"identity": identity, "state": state, "reason": str(exc), "remediation": "Inspect this identity and its dependencies; repair explicitly, then refresh."})
    for slot, occupants in slots.items():
        if len(occupants) > 1:
            findings.extend({"identity": identity, "slot": slot, "state": "conflicting", "reason": "More than one assertion occupies a single-value slot", "remediation": "Explicitly disposition all but the intended assertion."} for identity in occupants)
    for policy, evaluate in ((assessment, "assess"), (transcript, "transcript_reciprocity")):
        if policy is not None:
            from . import policies
            try:
                outcome = getattr(policies, evaluate)(policy, records, resolver, work=work)
            except BudgetExceeded:
                complete = False
                outcome = {"policy": evaluate, "state": "unknown", "reason": "work budget exhausted"}
            except (ValueError, KeyError, OSError) as exc:
                outcome = {"policy": evaluate, "state": "unknown", "reason": str(exc)}
            outcomes.append(outcome)
    # Exact persisted inputs are checked again before any positive policy result.
    from _common import document_revision_at
    for locator, expected in resolver.observations.items():
        if not locator.endswith(".md"):
            continue
        try:
            work.take()
            if document_revision_at(store.root / locator) != expected:
                complete = False
        except (OSError, BudgetExceeded):
            complete = False
            break
    for locator, expected in revisions.items():
        try:
            work.take()
            if digest((store.root / locator).read_bytes()) != expected:
                complete = False
        except (OSError, BudgetExceeded):
            complete = False
            break
    if store.directory_stamp() != start_directories:
        complete = False
    if not complete:
        findings.append({"state": "not-evaluated", "reason": "Source page incomplete, work budget exhausted or inputs changed during evaluation", "remediation": "Continue with the returned cursor, or select explicit kind:id identities."})
    if findings or not complete or cursor or identities:
        for outcome in outcomes:
            if outcome["state"] == "satisfied":
                outcome.update(state="unknown", reason="integrity or freshness could not be established")
    return {"records": outcomes, "findings": findings, "visited": work.visited, "complete": complete,
            "cursor": next_cursor, "source_freshness": "selected-input-revisions-verified" if complete and identities else "bounded-input-revisions-verified" if complete and not cursor else "observed-page-inputs"}
