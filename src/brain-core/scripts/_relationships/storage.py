"""Independent shared records with a disposable, incrementally maintained index.

The index is a locator/adjacency projection, never the authority for content.
Publication journals retain admitted event inputs for repair without state replay.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import uuid

from _bootstrap.file_transaction import FilePlan, apply_file_changes
from .model import Conflict, MAX_WORK, canonical, digest, identifier, node_key, slot_key, endpoint, validate_definition


KINDS = ("assertion", "occurrence", "endpoint", "event", "contract", "predicate", "event-type", "policy", "operation")
SCHEMA = "brain.relationship-record/1"
MAX_RECORD_BYTES = 262144


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def decode(raw):
    if len(raw) > MAX_RECORD_BYTES:
        raise ValueError("relationship record exceeds 256 KiB bound")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    try:
        return _validate_record(value)
    except ValueError as exc:
        context = str(value.get("kind")) + ":" + str(value.get("id")) if isinstance(value, dict) else "record"
        raise ValueError(context + ": " + str(exc)) from exc


def _validate_record(value, depth=0):
    if depth > 4:
        raise ValueError("recovery record nesting exceeds the supported envelope")
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("unsupported relationship record envelope")
    identifier(value.get("id"))
    if value.get("kind") not in KINDS:
        raise ValueError("unknown relationship record kind")
    kind = value["kind"]
    if "retired" in value and type(value["retired"]) is not bool:
        raise ValueError("retired must be boolean")
    required = {
        "assertion": ("subject", "predicate", "object", "scope", "revision"),
        "occurrence": ("subject", "predicate", "object", "scope", "revision"),
        "endpoint": ("node", "path", "born", "type"),
        "event": ("subject", "type", "payload", "contract_revision", "revision", "recorded_at", "provenance"),
        "predicate": ("name", "meaning", "contract_revision"),
        "event-type": ("name", "meaning", "contract_revision"),
        "contract": ("definition",),
        "policy": ("selected",),
        "operation": ("state",),
    }
    if not value.get("retired") and any(key not in value for key in required[kind]):
        raise ValueError("missing required fields for " + kind)
    for key in ("subject", "object", "node"):
        if key in value:
            endpoint(value[key])
    for key in ("predicate", "type", "scope", "revision", "contract_revision", "recorded_at", "path", "born", "meaning", "name"):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            raise ValueError("invalid record field: " + key)
    if "payload" in value and not isinstance(value["payload"], dict):
        raise ValueError("event payload must be an object")
    for name, item in value.get("payload", {}).items():
        identifier(name)
        if isinstance(item, dict):
            endpoint(item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("event payload field must be a scalar or endpoint: " + name)
    if value.get("corrects") is not None:
        identifier(value["corrects"])
    if kind in ("predicate", "event-type"):
        validate_definition(value)
    if kind == "contract":
        validate_definition(value.get("definition"))
    if kind == "operation":
        if value["state"] not in ("pending", "native-pending", "state-committed", "projection-pending", "complete", "abandoned"):
            raise ValueError("invalid recovery journal")
        if value["state"] not in ("complete", "abandoned") and any(
            not isinstance(value.get(key), list) for key in ("records", "events", "removals")
        ):
            raise ValueError("unresolved recovery journal requires captured inputs")
        if value["state"] not in ("complete", "abandoned"):
            for item in (*value["records"], *value["events"]):
                if not isinstance(item, dict):
                    raise ValueError("captured record must be an object")
                _validate_record({**item, "schema": SCHEMA}, depth + 1)
            for item in value["removals"]:
                if not isinstance(item, (list, tuple)) or len(item) != 2 or item[0] not in KINDS:
                    raise ValueError("captured removal must contain kind and identity")
                identifier(item[1])
    return value


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.shared = self.root / "_Relationships"
        self.config = self.root / "_Config" / "Relationships"
        self.index = self.root / ".brain" / "local" / "relationships.sqlite"

    def path(self, kind, identity):
        if kind not in KINDS:
            raise ValueError("unknown relationship storage kind")
        base = self.config if kind in ("predicate", "event-type", "policy") else self.shared
        locator = digest(identifier(identity).encode("utf-8")).removeprefix("sha256:")
        result = base / kind / (locator + ".json")
        return self._validate_path(result)

    def _validate_path(self, result):
        if result.resolve().is_relative_to(self.root) is False:
            raise ValueError("relationship storage escapes the selected Brain")
        if any(path.is_symlink() for path in (result, *result.parents) if path != self.root.parent):
            raise ValueError("relationship storage cannot use symbolic links")
        return result

    def read(self, kind, identity):
        path = self.path(kind, identity)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        value = decode(raw)
        if value["id"] != identity or value["kind"] != kind:
            raise ValueError("record identity disagrees with its storage locator")
        return value

    def read_path(self, path):
        """Read a source envelope and verify its opaque physical locator."""
        value = decode(self._validate_path(path).read_bytes())
        if path != self.path(value["kind"], value["id"]):
            raise ValueError("record identity disagrees with its storage locator")
        return value

    def revision(self, kind, identity):
        path = self.path(kind, identity)
        return digest(path.read_bytes()) if path.exists() else None

    def write(self, records, removals=()):
        plan = FilePlan()
        for record in records:
            record = {**record, "schema": SCHEMA}
            content = canonical(record) + "\n"
            decode(content.encode("utf-8"))
            plan.write_text(self.path(record["kind"], record["id"]), content)
        for kind, identity in removals:
            plan.delete(self.path(kind, identity))
        apply_file_changes(plan.changes())

    @contextmanager
    def connection(self):
        if not self.index.resolve().is_relative_to(self.root) or any(
            path.is_symlink() for path in (self.index, self.index.parent, self.index.parent.parent)
        ):
            raise ValueError("relationship index must remain inside the selected Brain without symlinks")
        self.index.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.index)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT, id TEXT, path TEXT, revision TEXT, stamp TEXT,
                    subject TEXT, object TEXT, predicate TEXT, scope TEXT, slot TEXT,
                    corrects TEXT, workspace TEXT, time TEXT, retired INTEGER,
                    PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS subject_idx ON records(subject,kind,id);
                CREATE INDEX IF NOT EXISTS object_idx ON records(object,kind,id);
                CREATE INDEX IF NOT EXISTS predicate_idx ON records(predicate,kind,id);
                CREATE INDEX IF NOT EXISTS slot_idx ON records(slot);
                CREATE INDEX IF NOT EXISTS correction_idx ON records(corrects);
                CREATE INDEX IF NOT EXISTS workspace_idx ON records(workspace,kind,id);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS payload_refs (target TEXT, kind TEXT, id TEXT);
                CREATE INDEX IF NOT EXISTS payload_target ON payload_refs(target);
            """)
            yield connection

    def stamp(self, path):
        info = path.stat()
        return f"{info.st_mtime_ns}:{info.st_size}:{info.st_ino}"

    def directory_stamp(self):
        return digest({str(path.relative_to(self.root)): self.stamp(path) if path.exists() else None
                       for base in (self.shared, self.config) for path in (base, *(base / kind for kind in KINDS))})

    def initialise(self):
        if not self.index.exists():
            existing = any(base.exists() and any(base.glob("*/*.json")) for base in (self.shared, self.config))
            if existing:
                raise Conflict("relationship projection missing; run relationship.refresh before mutation")
            self.reconcile(())

    def generation(self):
        self.initialise()
        with self.connection() as connection:
            values = dict(connection.execute("SELECT key,value FROM metadata"))
        return values.get("generation", "empty"), values.get("incomplete") == "true" or values.get("directories") != self.directory_stamp()

    def reconcile(self, records, removals=()):
        with self.connection() as connection:
            for kind, identity in removals:
                connection.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, identity))
                connection.execute("DELETE FROM payload_refs WHERE kind=? AND id=?", (kind, identity))
            for record in records:
                kind, identity = record["kind"], record["id"]
                path = self.path(kind, identity)
                subject_node = record.get("subject") or record.get("node")
                subject = node_key(subject_node) if subject_node else None
                obj = node_key(record["object"]) if record.get("object") else None
                connection.execute("DELETE FROM payload_refs WHERE kind=? AND id=?", (kind, identity))
                for value in record.get("payload", {}).values():
                    if isinstance(value, dict) and "kind" in value:
                        connection.execute("INSERT INTO payload_refs VALUES (?,?,?)", (node_key(value), kind, identity))
                if kind == "operation" and record["state"] not in ("complete", "abandoned"):
                    events = {item["id"] for item in (*record["records"], *record["events"]) if item["kind"] == "event"}
                    events.update(key for category, key in record["removals"] if category == "event")
                    for event in events:
                        connection.execute("INSERT INTO payload_refs VALUES (?,?,?)", ("event:" + event, kind, identity))
                connection.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    kind, identity, str(path.relative_to(self.root)), digest(path.read_bytes()), self.stamp(path),
                    subject, obj, record.get("predicate", record.get("type")), record.get("scope"),
                    slot_key(record) if kind == "assertion" and record.get("subject") else None, record.get("corrects"),
                    record.get("workspace", "global"), record.get("recorded_at"), bool(record.get("retired"))))
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('generation',?)", (uuid.uuid4().hex,))
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('directories',?)", (self.directory_stamp(),))

    def rows(self, *, budget=MAX_WORK, offset=0, exclude_ids=(), **filters):
        allowed = {"kind", "id", "subject", "object", "predicate", "scope", "slot", "corrects", "workspace", "retired"}
        if set(filters) - allowed:
            raise ValueError("unsupported relationship index selector")
        self.initialise()
        clauses, values = [], []
        if exclude_ids:
            clauses.append("id NOT IN (" + ",".join("?" for _ in exclude_ids) + ")")
            values.extend(exclude_ids)
        for name, value in filters.items():
            if isinstance(value, tuple):
                clauses.append(name + " IN (" + ",".join("?" for _ in value) + ")")
                values.extend(value)
            else:
                clauses.append(name + "=?")
                values.append(value)
        query = "SELECT * FROM records" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(query + " ORDER BY kind,id LIMIT ? OFFSET ?", (*values, budget + 1, offset))]

    def checked(self, row):
        path = self.path(row["kind"], row["id"])
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise Conflict("indexed relationship record is missing; refresh required: " + row["id"]) from exc
        if digest(raw) != row["revision"]:
            raise Conflict("relationship projection is stale; refresh required: " + row["id"])
        return decode(raw)

    def source_freshness(self, *, budget=MAX_WORK, kind=None):
        generation, stale = self.generation()
        rows = self.rows(budget=budget, **({"kind": kind} if kind else {}))
        if stale or len(rows) > budget:
            return False, generation, "source inventory changed or exceeds work budget"
        for row in rows:
            path = self.path(row["kind"], row["id"])
            try:
                if self.stamp(path) != row["stamp"]:
                    return False, generation, "source changed: " + row["id"]
            except FileNotFoundError:
                return False, generation, "source removed: " + row["id"]
        return True, generation, "observed source metadata only; no coherent filesystem snapshot"

    def source_page(self, *, budget, after=""):
        """Bound record reads and memory; explicit rebuild enumerates source names."""
        import heapq
        paths = (path for base in (self.shared, self.config) for path in base.glob("*/*.json")
                 if str(path.relative_to(self.root)) > after)
        return heapq.nsmallest(budget + 1, paths, key=lambda path: str(path.relative_to(self.root)))

    def refresh(self, *, budget=MAX_WORK, cursor=None):
        directories = self.directory_stamp()
        with self.connection() as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        if cursor is not None:
            if metadata.get("refresh_cursor") != cursor or metadata.get("refresh_directories") != directories:
                raise Conflict("refresh continuation changed; restart without cursor")
            after = metadata.get("refresh_after", "")
            prior_failures = int(metadata.get("refresh_failures", "0"))
        else:
            after, prior_failures = "", 0
            with self.connection() as connection:
                connection.execute("DELETE FROM records")
                connection.execute("DELETE FROM payload_refs")
                connection.execute("INSERT OR REPLACE INTO metadata VALUES ('incomplete','true')")
        paths = self.source_page(budget=budget, after=after)
        records, findings = [], []
        for path in paths[:budget]:
            try:
                record = decode(path.read_bytes())
                if path != self.path(record["kind"], record["id"]):
                    raise ValueError("record identity/path mismatch")
                records.append(record)
            except (ValueError, OSError, UnicodeError) as exc:
                findings.append({"path": str(path.relative_to(self.root)), "state": "malformed", "reason": str(exc)})
        self.reconcile(records)
        more = len(paths) > budget
        continuation = uuid.uuid4().hex if more else ""
        failures = prior_failures + len(findings)
        with self.connection() as connection:
            for key, value in {"incomplete": "true" if more or failures else "false",
                               "refresh_cursor": continuation, "refresh_directories": directories,
                               "refresh_after": str(paths[min(len(paths), budget)-1].relative_to(self.root)) if paths else after,
                               "refresh_failures": str(failures)}.items():
                connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, value))
        return {"complete": not more and not failures, "scan_finished": not more,
                "visited": min(len(paths), budget), "findings": findings, "total_findings": failures,
                "cursor": continuation or None, "source_freshness": "observed-source-scan",
                "enumeration": "Source filename enumeration is explicit rebuild work; content reads and retained memory are page-bounded."}

    def dependants(self, identity, *, budget=MAX_WORK):
        self.initialise()
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM records WHERE subject=? OR object=? OR predicate=? OR corrects=? OR (kind,id) IN (SELECT kind,id FROM payload_refs WHERE target=?) ORDER BY kind,id LIMIT ?",
                                      (identity, identity, identity.removeprefix("predicate:"), identity.removeprefix("event:"), identity, budget + 1))
            return [dict(row) for row in rows]

    def publish(self, operation, records, events=(), removals=()):
        """Persist admitted inputs first; only publish success after projection reconciliation."""
        self.initialise()
        previous = self.read("operation", operation) or {}
        originals = previous.get("originals", {item["kind"] + ":" + item["id"]: self.revision(item["kind"], item["id"])
                                               for item in (*records, *events)})
        originals.update({kind + ":" + identity: self.revision(kind, identity) for kind, identity in removals})
        journal = {**previous, "kind": "operation", "id": operation, "state": "pending", "records": records,
                   "events": list(events), "removals": list(removals), "originals": originals}
        self.write([journal])
        self.write(records, removals)
        journal["state"] = "state-committed"
        self.write([journal])
        self.write(events)
        journal["state"] = "projection-pending"
        self.write([journal])
        self.reconcile([*records, *events, journal], removals)
        journal = self._terminal_journal(journal, "complete")
        self.write([journal])
        # The journal is recovery metadata, not a graph input; graph generation
        # changes only once for this logical publication.
        self._reconcile_journal(journal)

    def _reconcile_journal(self, journal):
        path = self.path("operation", journal["id"])
        with self.connection() as connection:
            connection.execute("DELETE FROM payload_refs WHERE kind='operation' AND id=?", (journal["id"],))
            connection.execute("UPDATE records SET revision=?,stamp=? WHERE kind='operation' AND id=?",
                               (digest(path.read_bytes()), self.stamp(path), journal["id"]))
            connection.execute("INSERT OR REPLACE INTO metadata VALUES ('directories',?)", (self.directory_stamp(),))

    @staticmethod
    def _terminal_journal(journal, state, reason=None):
        """Keep an idempotence receipt without retaining cleaned event payloads."""
        receipt = {"kind": "operation", "id": journal["id"], "state": state}
        if reason is not None:
            receipt["resolution_reason"] = reason
        return receipt

    def repair(self, operation):
        journal = self.read("operation", operation)
        if journal is None:
            raise ValueError("unknown operation")
        if journal["state"] == "abandoned":
            raise Conflict("operation was explicitly abandoned; it cannot be replayed")
        if journal["state"] == "complete":
            return 0
        state_committed = journal["state"] in ("state-committed", "projection-pending")
        if journal.get("native_guards") and not state_committed:
            for locator, expected in journal["native_guards"].items():
                path = (self.root / locator).resolve()
                if not path.is_relative_to(self.root):
                    raise ValueError("native recovery locator escapes selected Brain")
                actual = digest(path.read_bytes()) if path.exists() else None
                if actual != expected:
                    raise Conflict("native state has not fully committed or has since changed; cannot replay: " + locator)
            for intended in journal["records"]:
                actual = self.read(intended["kind"], intended["id"])
                if actual != {**intended, "schema": SCHEMA}:
                    before = journal["originals"].get(intended["kind"] + ":" + intended["id"])
                    if self.revision(intended["kind"], intended["id"]) != before:
                        raise Conflict("semantic state changed since native admission")
            self.write(journal["records"])
        if not state_committed:
            for intended in journal["records"]:
                actual = self.read(intended["kind"], intended["id"])
                if actual != {**intended, "schema": SCHEMA}:
                    raise Conflict("repair cannot replay changed or uncommitted state: " + intended["id"])
            for kind, identity in journal["removals"]:
                if self.read(kind, identity) is not None:
                    raise Conflict("repair cannot replay an uncommitted removal")
        missing = []
        for event in journal["events"]:
            actual = self.read("event", event["id"])
            if actual is None:
                missing.append(self._current_event_locators(event))
            elif actual.get("revision") != event["revision"]:
                raise Conflict("repair event identity already has different content")
        self.write(missing)
        journal["state"] = "projection-pending"
        self.write([journal])
        # A durable state-committed marker proves the captured event happened.
        # Later supported edits may have changed those records or native files;
        # refresh their current projection, never replay the old record images.
        current_records, absent = [], []
        targets = {(item["kind"], item["id"]) for item in (*journal["records"], *journal["events"])}
        targets.update(tuple(item) for item in journal["removals"])
        for kind, identity in sorted(targets):
            actual = self.read(kind, identity)
            if actual is None:
                absent.append((kind, identity))
            else:
                current_records.append(actual)
        self.reconcile([*current_records, journal], absent)
        journal = self._terminal_journal(journal, "complete")
        self.write([journal])
        self._reconcile_journal(journal)
        return len(missing)

    def _current_event_locators(self, event):
        """Maintain typed locators by retained identity, never rewrite event meaning."""
        from .model import event_revision
        def current(node):
            if not isinstance(node, dict) or node.get("kind") != "artefact" or not node.get("identity"):
                return node
            rows = self.rows(kind="endpoint", subject=node_key(node), retired=0, budget=1)
            if len(rows) > 1:
                raise Conflict("ambiguous current endpoint binding for captured history")
            return self.checked(rows[0])["node"] if rows else node
        updated = {**event, "subject": current(event["subject"]),
                   "payload": {key: current(value) for key, value in event["payload"].items()}}
        if event_revision(updated) != event["revision"]:
            raise Conflict("captured event locator maintenance would change its logical revision")
        return updated

    def abandon(self, operation, reason):
        """Resolve a failed intent only with exact proof of unchanged before-state."""
        journal = self.read("operation", operation)
        if journal is None or journal["state"] in ("complete", "abandoned"):
            raise Conflict("only an unresolved operation may be abandoned")
        if "originals" not in journal or (journal.get("native_guards") and "native_before" not in journal):
            raise Conflict("before-state proof is unavailable; abandonment is unsafe")
        for locator, expected in journal.get("native_before", {}).items():
            path = self.root / locator
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("native recovery locator escapes selected Brain")
            if (digest(path.read_bytes()) if path.exists() else None) != expected:
                raise Conflict("native state changed; abandonment would hide committed effects")
        for key, expected in journal["originals"].items():
            kind, identity = key.split(":", 1)
            if self.revision(kind, identity) != expected:
                raise Conflict("semantic state changed; abandonment would hide committed effects")
        journal = self._terminal_journal(journal, "abandoned", reason)
        self.write([journal])
        self.reconcile([journal])
        return {"operation": operation, "state": "abandoned", "reason": reason, "state_replayed": False}
