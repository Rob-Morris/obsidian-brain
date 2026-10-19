"""Bounded graph inspection with explicit projection/source freshness."""

from dataclasses import asdict, replace

from _relationships.definitions import BUILTINS, capture_policy, definition
from _relationships.endpoints import Endpoints
from _relationships.model import Conflict, BudgetExceeded, canonical, digest, node_key, slot_key
from _relationships.service import interpreted_event, HistoryView
from _relationships.storage import Store
from .._read_support import catalogue_entry as read_entry
from ..preparation import ResultReadPreparation, execute_prepared_read
from ..results import ErrorCode, Ok, request_error
from ._types import GraphCursor, GraphResult


def read(context, request, *, kind):
    store = Store(context.selected_brain.vault_root)
    try:
        if kind == "event":
            record = interpreted_event(store, request.identity, budget=request.work_budget)
        else:
            record = store.read(request.record_kind.value, request.identity)
            if record is None:
                raise ValueError("unknown relationship identity")
            if record["kind"] == "assertion" and not record.get("retired"):
                rows = store.rows(kind="assertion", slot=slot_key(record), retired=0)
                record = {**record, "slot_revision": digest(sorted((row["id"], store.checked(row)["revision"]) for row in rows))}
            record = {**record, "document_revision": store.revision(record["kind"], record["id"])}
        generation, stale = store.generation()
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, GraphResult((record,), generation=generation,
                  complete=not stale, source_freshness="direct-record; dependencies use observed projection"))
    except (ValueError, OSError) as exc:
        return request_error(type(request), ErrorCode.CONFLICT if isinstance(exc, Conflict) else ErrorCode.INVALID_REQUEST, str(exc))


def _native_facts(store, resolver, subject, filters, budget):
    """Read owner facts without allocating occurrence or endpoint records."""
    from _common import parse_frontmatter, read_exact_file_content
    from _relationships.endpoints import binding_id
    from _relationships.native import facts
    binding = store.read("endpoint", binding_id(subject["reference"])) or resolver.bindings[binding_id(subject["reference"])]
    fields, _ = parse_frontmatter(read_exact_file_content(store.root / binding["path"]))
    values = facts(subject["reference"], fields)
    if len(values) > budget:
        raise BudgetExceeded("native subject facts exceed work budget; increase work_budget")
    result = []
    for predicate, obj in values:
        if filters.get("predicate", predicate) != predicate or filters.get("scope", "global") != "global":
            continue
        obj = resolver.resolve(obj)
        workspace = resolver.workspace(subject)
        if filters.get("object", node_key(obj)) != node_key(obj) or filters.get("workspace", workspace) != workspace:
            continue
        matching = store.rows(kind="occurrence", subject=node_key(subject), predicate=predicate,
                              object=node_key(obj), retired=0, budget=1)
        record = {"kind": "native-fact", "subject": subject, "predicate": predicate, "object": obj,
                  "scope": "global", "workspace": workspace, "materialised": False}
        if len(matching) == 1:
            occurrence = store.checked(matching[0])
            record.update(id=occurrence["id"], revision=occurrence["revision"], materialised=True)
        result.append(record)
    return result


def query(context, request, *, kind):
    store = Store(context.selected_brain.vault_root)
    try:
        generation, stale = store.generation()
        selectors = asdict(request)
        selectors.pop("cursor", None)
        selector_digest = digest(selectors)
        offset = request.cursor.offset if request.cursor else 0
        if type(offset) is not int or offset < 0:
            raise ValueError("cursor offset must be a non-negative integer")
        filters = {"kind": kind, "retired": 0}
        for name in ("predicate", "scope", "workspace"):
            value = getattr(request, name, None)
            if value is not None:
                filters[name] = value
        from _lifecycle.derived_cache_state import require_fresh_compiled_router
        resolver = Endpoints(store, require_fresh_compiled_router(store.root))
        resolved = {}
        for name in ("subject", "object"):
            value = getattr(request, name, None)
            if value is not None:
                resolved[name] = resolver.resolve(asdict(value))
                filters[name] = node_key(resolved[name])
        native = []
        native_enabled = kind == "assertion" and request.include_native
        scoped_native = native_enabled and resolved.get("subject", {}).get("kind") == "artefact"
        if scoped_native:
            native = _native_facts(store, resolver, resolved["subject"], filters, request.work_budget)
            # Native edits before exposure need a cursor guard too. Bind the
            # evaluated facts, so body-only edits do not invalidate graph pages.
            generation = digest([generation, native])
        elif native_enabled:
            filters["kind"] = ("assertion", "occurrence")
        if request.cursor and (request.cursor.generation != generation or request.cursor.selectors != selector_digest):
            raise Conflict("query cursor is stale or belongs to different selectors; restart without cursor")
        if getattr(request, "depth", 1) > 1:
            return _traverse(store, request, filters, native, generation, selector_digest, stale, scoped_native)
        flat = getattr(request, "depth", 1) == 1
        rows = store.rows(budget=request.work_budget, offset=max(0, offset - len(native)) if flat else 0, **filters)
        roots = native + rows if not flat else native[offset:] + rows
        history = HistoryView(store, request.work_budget) if kind == "event" else None
        records, findings, visited, output_bytes = [], [], 0, 0
        pending = [(row, 1) for row in roots]
        seen = set()
        while pending and visited < request.work_budget:
            row, level = pending.pop(0)
            key = (row["kind"], row.get("id", canonical([row.get("predicate"), row.get("object")])))
            if key in seen:
                continue
            seen.add(key)
            visited += 1
            try:
                record = row if row["kind"] == "native-fact" else store.checked(row)
                if kind == "event":
                    from datetime import datetime
                    if request.date_from and datetime.fromisoformat(record["recorded_at"]) < datetime.fromisoformat(request.date_from):
                        continue
                    if request.date_to and datetime.fromisoformat(record["recorded_at"]) > datetime.fromisoformat(request.date_to):
                        continue
                    record = history.read(row["id"])
                if flat:
                    size = len(canonical(record).encode("utf-8"))
                    if output_bytes + size > 262144:
                        visited -= 1
                        pending.insert(0, (row, level))
                        findings.append({"identity": row.get("id"), "state": "incomplete", "reason": "256 KiB output budget reached; continue, or read this identity directly if it exceeds one page"})
                        break
                    output_bytes += size
                records.append(record)
                if flat and len(records) >= request.limit:
                    break
                if level < getattr(request, "depth", 1) and record.get("id"):
                    prefix = "occurrence:" if row["kind"] in ("occurrence", "native-fact") else "relationship:"
                    following = store.rows(kind="assertion", subject=prefix + row["id"], retired=0,
                                           budget=request.work_budget - visited)
                    pending.extend((item, level + 1) for item in following)
            except BudgetExceeded:
                visited -= 1
                pending.insert(0, (row, level))
                findings.append({"state": "incomplete", "reason": "history work budget exhausted"})
                break
            except (ValueError, OSError) as exc:
                findings.append({"identity": row["id"], "state": "stale" if isinstance(exc, Conflict) else "unavailable", "reason": str(exc)})
        page, output_bytes = [], 0
        for record in records[0 if flat else offset:(0 if flat else offset) + request.limit]:
            size = len(canonical(record).encode("utf-8"))
            if output_bytes + size > 262144:
                findings.append({"state": "incomplete", "reason": "256 KiB output budget reached; narrow selectors or read the identity directly"})
                break
            page.append(record)
            output_bytes += size
        page = tuple(page)
        next_offset = offset + (visited if flat else len(page))
        has_more = bool(pending) if flat else next_offset < len(records)
        cursor = GraphCursor(generation, next_offset, selector_digest) if has_more else None
        # In-place edits to omitted rows cannot be certified by a cached query.
        # relationship.check is the explicit bounded source-integrity scan.
        findings.append({"state": "not-evaluated", "reason": "External edits outside the visited projection inputs were not inspected; use relationship.check."})
        if native_enabled and not scoped_native:
            findings.append({"state": "not-evaluated", "reason": "Native scope: only materialised occurrences were inspected. Supply an artefact subject to evaluate its current native owner-backed facts."})
        if stale or pending:
            findings.append({"state": "stale" if stale else "incomplete", "reason": "Refresh required" if stale else "work budget exhausted"})
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, GraphResult(page, tuple(findings), generation=generation,
                  complete=False, source_freshness="observed-projection-only", visited=history.work.visited if history else visited, next_cursor=cursor))
    except (ValueError, OSError) as exc:
        return request_error(type(request), ErrorCode.CONFLICT if isinstance(exc, Conflict) else ErrorCode.INVALID_REQUEST, str(exc))


def _traverse(store, request, filters, native, generation, selectors, stale, scoped_native):
    """Continue depth-first subject adjacency with at most one frame per level.

    Assertions each have one subject. A descendant that also matches the root
    selector is left to the root stream, avoiding duplicate subtrees without an
    ever-growing global seen set in the continuation.
    """
    import base64
    import json
    frames = [{"subject": None, "offset": 0}]
    if request.cursor:
        try:
            frames = json.loads(base64.urlsafe_b64decode(request.cursor.traversal or ""))
            if not isinstance(frames, list) or not 1 <= len(frames) <= request.depth:
                raise ValueError("invalid frame count")
            for index, frame in enumerate(frames):
                if not isinstance(frame, dict) or set(frame) != {"subject", "offset"}:
                    raise ValueError("invalid traversal frame")
                if type(frame["offset"]) is not int or frame["offset"] < 0:
                    raise ValueError("invalid traversal offset")
                subject = frame["subject"]
                if (index == 0 and subject is not None) or (index and (
                    not isinstance(subject, str) or len(subject) > 160 or not subject.startswith(("relationship:", "occurrence:"))
                )):
                    raise ValueError("invalid traversal subject")
        except (ValueError, TypeError) as exc:
            raise Conflict("invalid traversal cursor; restart without cursor") from exc
    records, findings, visited, output_bytes = [], [], 0, 0
    while frames and visited < request.work_budget and len(records) < request.limit:
        frame = frames[-1]
        is_root = len(frames) == 1
        if is_root and frame["offset"] < len(native):
            row = native[frame["offset"]]
        else:
            query_filters = filters if is_root else {"kind": "assertion", "subject": frame["subject"], "retired": 0}
            rows = store.rows(budget=0, offset=frame["offset"] - (len(native) if is_root else 0), **query_filters)
            if not rows:
                frames.pop()
                continue
            row = rows[0]
        visited += 1
        if not is_root and all(row.get(key) in value if isinstance(value, tuple) else row.get(key) == value
                               for key, value in filters.items()):
            frame["offset"] += 1
            continue
        try:
            record = row if row["kind"] == "native-fact" else store.checked(row)
            size = len(canonical(record).encode("utf-8"))
            if output_bytes + size > 262144:
                findings.append({"identity": row.get("id"), "state": "incomplete",
                                 "reason": "256 KiB output budget reached; continue or read this identity directly"})
                break
            records.append(record)
            output_bytes += size
            frame["offset"] += 1
            prefix = "occurrence:" if row["kind"] in ("occurrence", "native-fact") else "relationship:"
            child_subject = prefix + record["id"] if record.get("id") else None
            if len(frames) < request.depth and child_subject and child_subject not in {item["subject"] for item in frames}:
                frames.append({"subject": child_subject, "offset": 0})
        except (ValueError, OSError) as exc:
            frame["offset"] += 1
            findings.append({"identity": row.get("id"), "state": "unavailable", "reason": str(exc)})
    cursor = None
    if frames:
        state = base64.urlsafe_b64encode(canonical(frames).encode()).decode()
        cursor = GraphCursor(generation, frames[0]["offset"], selectors, state)
    findings.append({"state": "not-evaluated", "reason": "External edits outside visited projection inputs were not inspected; use relationship.check."})
    if request.include_native and not scoped_native:
        findings.append({"state": "not-evaluated", "reason": "Native scope: only materialised occurrences; select an artefact subject for current owner facts."})
    if stale:
        findings.append({"state": "stale", "reason": "Refresh required"})
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, GraphResult(tuple(records), tuple(findings),
              generation=generation, complete=False, source_freshness="observed-projection-only", visited=visited, next_cursor=cursor))


def definitions(context, request):
    store = Store(context.selected_brain.vault_root)
    try:
        generation, stale = store.generation()
        selectors = digest([request.definition_kind.value, request.limit, request.work_budget])
        if request.cursor and (request.cursor.generation != generation or request.cursor.selectors != selectors):
            raise Conflict("catalogue cursor is stale; restart without cursor")
        offset = request.cursor.offset if request.cursor else 0
        builtin_ids = tuple(sorted(BUILTINS)) if request.definition_kind.value == "event-type" else ()
        count = min(request.limit, request.work_budget)
        candidates = [(key, None) for key in builtin_ids[offset:offset + count + 1]]
        if len(candidates) <= count:
            rows = store.rows(kind=request.definition_kind.value, budget=count - len(candidates),
                              offset=max(0, offset - len(builtin_ids)), exclude_ids=builtin_ids)
            candidates.extend((row["id"], row) for row in rows)
        page, output_bytes = [], 0
        for key, row in candidates[:count]:
            value = store.checked(row) if row is not None else definition(store, "event-type", key)
            value = {**value, "document_revision": store.revision(request.definition_kind.value, key)}
            size = len(canonical(value).encode("utf-8"))
            if output_bytes + size > 262144:
                break
            page.append(value)
            output_bytes += size
        next_offset = offset + len(page)
        cursor = GraphCursor(generation, next_offset, selectors) if len(page) < len(candidates) else None
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, GraphResult(tuple(page), generation=generation,
                  complete=False, source_freshness="observed-projection-only", visited=min(count, len(candidates)), next_cursor=cursor,
                  findings=({"state": "stale" if stale else "not-evaluated", "reason": "Unvisited external source edits are not certified by catalogue discovery."},)))
    except (ValueError, OSError) as exc:
        return request_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))


def entry(request_type, reader, summary):
    from functools import partial
    return replace(read_entry(request_type, partial(execute_prepared_read, reader=reader)),
                   preparation=ResultReadPreparation(reader), summary=summary)
