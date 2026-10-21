"""Typed native resolution and continuity for referenced artefacts only.

Bindings retain the optional created marker and maintained locator of a referenced
artefact. They do not allocate identities for every document in the vault.
"""

from pathlib import Path

from .model import Conflict, digest, endpoint, node_key


def binding_id(reference):
    return "ref-" + digest(reference)[7:]


def native_reference(router, path, fields):
    from _common import canonical_living_artefact_key, resolve_type
    if not isinstance(fields.get("type"), str):
        return path
    try:
        definition = resolve_type(router, fields.get("type"))
    except ValueError:
        return path
    return canonical_living_artefact_key(definition, fields) or path


class Endpoints:
    def __init__(self, store, router, *, work=None):
        self.store, self.router = store, router
        self.work = work
        self.cache, self.bindings, self.observations = {}, {}, {}
        self.pending = set()
        self.workspaces = {}
        # Native mutation planning may reuse only its explicitly selected,
        # revision-bearing before-images. Generic reads remain strict.
        self.native_sources = {}

    def resolve(self, raw):
        node = endpoint(raw)
        key = node_key(node)
        if self.work and key not in self.cache:
            self.work.take()
        if key in self.cache:
            resolved = self.cache[key]
        elif node["kind"] == "artefact":
            resolved = self._artefact(node)
            self.cache[key] = resolved
        elif node["kind"] == "literal":
            resolved = node
        elif key in self.pending:
            resolved = node
        elif node["kind"] == "predicate":
            from .definitions import definition
            record = definition(self.store, "predicate", node["reference"])
            self.observations[key] = record["contract_revision"]
            resolved = node
        else:
            kind = {"relationship": "assertion", "occurrence": "occurrence", "event": "event"}[node["kind"]]
            record = self.store.read(kind, node["reference"])
            if record is None or record.get("retired"):
                raise ValueError("unavailable endpoint: " + key)
            self.observations[key] = self.store.revision(kind, node["reference"])
            self.workspaces[key] = record.get("workspace", "global")
            resolved = node
        if raw.get("identity") and resolved.get("identity") != raw["identity"]:
            raise Conflict("endpoint identity changed; explicit disposition is required")
        return resolved

    def _artefact(self, node):
        from _common import (resolve_and_validate_folder, resolve_and_check_bounds,
                             parse_frontmatter, read_exact_file_content, canonical_living_artefact_key)
        reference = node["reference"]
        existing = self.store.read("endpoint", binding_id(reference))
        retired_binding = existing if existing and existing.get("retired") else None
        if retired_binding:
            existing = None
        archived_path = existing["path"] if existing and existing["path"].startswith("_Archive/") else reference
        if archived_path.startswith("_Archive/"):
            from _portable.vault_files import read_archived_artefact
            content = read_archived_artefact(self.store.root, archived_path)
            if not isinstance(content, str):
                raise ValueError("archived endpoint cannot be resolved")
            path = archived_path
            fields, _ = parse_frontmatter(content)
            canonical = reference
        else:
            path, art = resolve_and_validate_folder(self.store.root, self.router, reference)
            source = Path(resolve_and_check_bounds(self.store.root / path, self.store.root))
            content = self.native_sources.get(path)
            if content is None:
                content = read_exact_file_content(source)
            fields, _ = parse_frontmatter(content)
            canonical = canonical_living_artefact_key(art, fields) or path
        born = str(fields.get("created") or "unrecorded")
        existing = existing or self.store.read("endpoint", binding_id(canonical))
        if existing and existing.get("retired"):
            retired_binding = existing
            existing = None
        if retired_binding:
            rows = self.store.dependants(node_key(retired_binding["node"]))
            if len(rows) > 5000 or any(row["kind"] in ("assertion", "occurrence") and not self.store.checked(row).get("retired") for row in rows):
                raise Conflict("previous endpoint has active dependencies; disposition them before authoring a fresh binding")
        if existing and (existing["born"] != born or existing["type"] != fields.get("type")):
            raise Conflict("artefact at this locator has changed identity: " + path)
        identity = existing["node"]["identity"] if existing else digest(
            [canonical, born, fields.get("type"), retired_binding["node"]["identity"]] if retired_binding
            else [canonical, born, fields.get("type")])
        resolved = {"kind": "artefact", "reference": canonical, "value": None, "identity": identity}
        binding = {"kind": "endpoint", "id": binding_id(canonical), "node": resolved,
                   "path": path, "born": born, "type": fields.get("type"), "retired": False}
        if not existing:
            self.bindings[binding["id"]] = binding
        self.observations[path] = content.revision
        from _common._workspace import membership
        self.workspaces[node_key(resolved)] = membership(canonical, fields) or "global"
        return resolved

    def workspace(self, node):
        if node_key(node) in self.workspaces:
            return self.workspaces[node_key(node)]
        if node["kind"] != "artefact":
            kind = {"relationship": "assertion", "occurrence": "occurrence", "event": "event"}.get(node["kind"])
            record = self.store.read(kind, node["reference"]) if kind else None
            return record.get("workspace", "global") if record else "global"
        from _common._workspace import membership
        entry = self.router.get("artefact_index", {}).get(node["reference"])
        return (membership(node["reference"], entry) if entry else None) or "global"
