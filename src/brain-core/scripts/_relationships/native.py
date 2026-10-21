"""Relationship projection of native-owned facts and lifecycle write plans."""

from dataclasses import asdict
import uuid

from .endpoints import binding_id, native_reference
from .model import Conflict, assertion_revision, canonical, digest, node_key


def facts(reference, fields):
    from _common._workspace import membership, normalise_tags
    values = []
    if fields.get("parent"):
        values.append(("native.parent", {"kind": "artefact", "reference": fields["parent"], "value": None}))
    workspace = membership(reference, fields)
    if workspace:
        values.append(("native.workspace", {"kind": "artefact", "reference": workspace, "value": None}))
    for tag in normalise_tags(fields.get("tags", [])):
        if tag.startswith("workspace/"):
            values.append(("native.relevance", {"kind": "artefact", "reference": tag, "value": None}))
    if fields.get("status") is not None:
        values.append(("native.status", {"kind": "literal", "reference": "", "value": fields["status"]}))
    return values


def expose(service, request):
    from _common import parse_frontmatter, read_exact_file_content
    subject = service.endpoints.resolve(asdict(request.subject))
    if subject["kind"] != "artefact":
        raise ValueError("native facts require an artefact subject")
    binding = service.store.read("endpoint", binding_id(subject["reference"])) or service.endpoints.bindings[binding_id(subject["reference"])]
    fields, _ = parse_frontmatter(read_exact_file_content(service.store.root / binding["path"]))
    current = service.store.rows(kind="occurrence", subject=node_key(subject), retired=0)
    existing = [service.store.checked(row) for row in current]
    result = []
    for predicate, obj in facts(subject["reference"], fields):
        obj = service.endpoints.resolve(obj)
        same = next((item for item in existing if item["predicate"] == predicate and node_key(item["object"]) == node_key(obj)), None)
        if same:
            result.append(same)
            continue
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, service.plan.operation + ":" + predicate + ":" + node_key(obj)))
        record = {"kind": "occurrence", "id": identity, "subject": subject, "predicate": predicate,
                  "object": obj, "scope": "global", "workspace": service.endpoints.workspace(subject), "retired": False}
        record["revision"] = assertion_revision(record)
        service.plan.records.append(record)
        result.append(record)
    represented = {item["id"] for item in result}
    closure = set()
    for record in existing:
        if record["id"] not in represented:
            closure.update(service._closure("occurrence", record["id"]))
            service.plan.records.append({**record, "retired": True})
    service._apply_dispositions(closure, request.dispositions)
    service.plan.result = {"items": result}
    return service._finish()


def plan_transition(service, request, native_plan):
    """Compose typed-reference maintenance and history before native admission."""
    from _common import parse_frontmatter, read_exact_file_content, serialize_frontmatter

    root, router, store = service.store.root, service.endpoints.router, service.store
    movement = getattr(native_plan, "movement", native_plan)
    writes = {item["path"]: item for item in getattr(native_plan, "writes", ())}
    moves = {item["source"]: item["dest"] for item in getattr(movement, "moves", ()) if item["source"].endswith(".md")}
    deleted = set(getattr(movement, "paths", ()))
    paths = set(writes) | set(moves) | deleted
    native_guards = {}
    relocations, changed_facts, changed_subjects, retired_seeds = {}, [], [], []
    renamed_references = {}
    sources = {path: read_exact_file_content(root / path, convert_lossless=True)
               for path in paths if path.endswith(".md")}
    service.endpoints.native_sources.update(sources)
    for old_path in paths:
        if old_path in deleted or not old_path.endswith(".md"):
            continue
        old_fields, _ = parse_frontmatter(sources[old_path])
        new_fields = writes.get(old_path, {"fields": old_fields})["fields"]
        old_reference = native_reference(router, old_path, old_fields)
        new_reference = native_reference(router, moves.get(old_path, old_path), new_fields)
        if old_reference != new_reference and old_fields.get("type") == new_fields.get("type"):
            renamed_references[new_reference] = old_reference
    for path in sorted(paths):
        if not path.endswith(".md"):
            continue
        raw = sources[path]
        before, body = parse_frontmatter(raw)
        after = None if path in deleted else writes.get(path, {"fields": before})["fields"]
        reference = native_reference(router, path, before)
        destination = moves.get(path, path)
        new_reference = native_reference(router, destination, after) if after else None
        # A pure move preserves bytes; serialising its frontmatter would invent
        # an expected rewrite that the native executor never performs.
        intended = (None if after is None else
                    serialize_frontmatter(after, body=writes[path]["body"]) if path in writes else raw)
        link_write = next((item for item in movement.links.writes if item.path in (path, destination)), None)
        if link_write:
            intended = link_write.after
        if path in moves:
            native_guards[path] = None
        native_guards[destination] = digest(intended.encode("utf-8")) if intended is not None else None
        binding = store.read("endpoint", binding_id(reference)) or store.read("endpoint", binding_id(path))
        old_status, new_status = before.get("status"), after.get("status") if after else None
        lifecycle = (request.COMMAND_ID in {"artefact.archive", "artefact.unarchive", "artefact.convert", "artefact.delete"}
                     or after is None or before.get("type") != after.get("type"))
        changed_status = old_status != new_status
        if not binding and (changed_status or lifecycle):
            # Capture the pre-mutation native identity while it is resolvable.
            subject = service.endpoints.resolve({"kind": "artefact", "reference": reference, "value": None})
            binding = service.endpoints.bindings.get(binding_id(subject["reference"]))
        if not binding:
            continue
        service.plan.observations["endpoint:" + binding["id"]] = store.revision("endpoint", binding["id"])
        subject = binding["node"]
        from _common._workspace import membership
        service.endpoints.workspaces[node_key(subject)] = membership(new_reference or reference, after or before) or "global"
        identity_change = after is None or before.get("type") != after.get("type")
        if identity_change:
            retired_seeds.append(("artefact", subject["identity"]))
            updated_binding = {**binding, "retired": True}
        else:
            new_reference = new_reference or reference
            updated_node = {**subject, "reference": new_reference}
            updated_binding = {**binding, "id": binding_id(new_reference), "node": updated_node, "path": destination}
            relocations[node_key(subject)] = updated_node
            if updated_binding["id"] != binding["id"]:
                service.plan.records.append({**binding, "retired": True})
        service.endpoints.bindings.pop(binding["id"], None)
        if binding != updated_binding or store.read("endpoint", binding["id"]) is None:
            service.plan.records.append(updated_binding)
        rows = store.rows(kind="occurrence", subject=node_key(subject), retired=0)
        if len(rows) > 1000:
            raise ValueError("native occurrence budget exceeded")
        desired = facts(new_reference, after) if rows and after and not identity_change else []
        desired_values = set()
        for predicate, obj in desired:
            obj = {**obj, "reference": renamed_references.get(obj["reference"], obj["reference"])}
            desired_values.add((predicate, node_key(service.endpoints.resolve(obj))))
        for row in rows:
            record = store.checked(row)
            obj = record["object"]
            if (record["predicate"], node_key(obj)) not in desired_values:
                retired_seeds.append(("occurrence", record["id"]))
                service.plan.records.append({**record, "retired": True})
                changed_facts.append(record["id"])
        if changed_status or lifecycle:
            service._selected("native-transition", subject, {"command": request.COMMAND_ID,
                "before": canonical({"status": old_status, "path": path, "type": before.get("type")}),
                "after": canonical({"status": new_status, "path": destination if after else None, "type": after.get("type") if after else None})})
            changed_subjects.append(reference)
    closure = set()
    for kind, identity in retired_seeds:
        closure.update(service._closure(kind, identity))
    service._apply_dispositions(closure, getattr(request, "relationship_dispositions", ()))
    # Existing reference-bearing graph records are transformed once per affected
    # endpoint, using adjacency rather than a per-edge whole-vault scan.
    transformed = {}
    already_retired = {(item["kind"], item["id"]) for item in service.plan.records if item.get("retired")}
    for old_key, new_node in relocations.items():
        rows = store.dependants(old_key)
        if len(rows) > 5000:
            raise ValueError("typed reference maintenance budget exceeded")
        for row in rows:
            key = (row["kind"], row["id"])
            if key in already_retired:
                continue
            record = transformed.get(key) or store.checked(row)
            original_record = record
            service.plan.observations[row["kind"] + ":" + row["id"]] = row["revision"]
            for field in ("subject", "object"):
                if record.get(field) and node_key(record[field]) == old_key:
                    record = {**record, field: new_node}
                    if field == "subject" and record["kind"] != "event":
                        record["workspace"] = service.endpoints.workspace(new_node)
            if record.get("payload"):
                record = {**record, "payload": {name: new_node if isinstance(value, dict) and "kind" in value and node_key(value) == old_key else value
                                                for name, value in record["payload"].items()}}
            if record != original_record:
                transformed[key] = record
    pending_scopes = [record for record in transformed.values() if record["kind"] in ("assertion", "occurrence")]
    seen_scopes = set()
    while pending_scopes:
        parent = pending_scopes.pop()
        key = (parent["kind"], parent["id"])
        if key in seen_scopes:
            continue
        seen_scopes.add(key)
        if len(seen_scopes) > 5000:
            raise ValueError("inherited workspace maintenance budget exceeded")
        node = ("relationship:" if parent["kind"] == "assertion" else "occurrence:") + parent["id"]
        service.endpoints.workspaces[node] = parent["workspace"]
        for row in store.rows(kind="assertion", subject=node, retired=0, budget=5000 - len(seen_scopes)):
            child = transformed.get(("assertion", row["id"])) or store.checked(row)
            if child.get("workspace") != parent["workspace"]:
                updated = {**child, "workspace": parent["workspace"]}
                transformed[("assertion", child["id"])] = updated
                service.plan.observations["assertion:" + row["id"]] = row["revision"]
                pending_scopes.append(updated)
    service.plan.records.extend(transformed.values())
    if changed_facts:
        service._selected("relationship-change", {"kind": "occurrence", "reference": changed_facts[0], "value": None},
                          {"affected": canonical(changed_facts), "dispositions": canonical([asdict(item) for item in getattr(request, "relationship_dispositions", ())])})
    service.plan.result = {"native_guards": native_guards, "changed_subjects": changed_subjects}
    return service._finish()
