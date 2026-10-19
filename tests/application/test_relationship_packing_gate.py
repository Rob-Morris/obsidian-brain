"""W1/W3: identical bounded native workflow, two disposable packing adapters.

This measures persistence mechanics, not production relationship semantics.
The product acceptance suite must separately prove the integrated commands.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from statistics import median
from time import perf_counter

import pytest

from _application._mutation_support import InlineContent
from _application.artefact.create import ArtefactCreateRequest
from _application.artefact.read import ArtefactReadRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.workspace.create import WorkspaceCreateRequest
from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _common import vault_mutation_lock
from command_application import application_for


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def revision(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class PackingProbe:
    def __init__(self, root, layout):
        self.root, self.layout = root, layout
        self.directory = root / ".brain" / "packing-probe" / layout
        self.directory.mkdir(parents=True)
        self.written_bytes = 0
        self.reconciliations = 0

    def load(self):
        records = {}
        for path in sorted(self.directory.glob("*.json")):
            records.update(json.loads(path.read_text()))
        return records

    def apply(self, records, expected):
        with vault_mutation_lock(self.root):
            current = self.load()
            if any(revision(current.get(key)) != value for key, value in expected.items()):
                return "conflict"
            merged = {**current, **records}
            plan = FilePlan()
            buckets = {}
            for key, value in merged.items():
                bucket = key if self.layout == "independent" else value["bucket"]
                buckets.setdefault(bucket, {})[key] = value
            for bucket, rows in buckets.items():
                plan.write_text(self.directory / (bucket + ".json"), encoded(rows))
            changes = plan.changes()
            self.written_bytes += sum(len(change.after or b"") for change in changes)
            apply_file_changes(changes)
            self.reconciliations += 1
            # Equivalent derived adjacency work, separate from the packing cost.
            self.adjacency = {}
            for key, record in merged.items():
                self.adjacency.setdefault(record["subject"], []).append(key)
            return "ok"


@pytest.mark.parametrize("layout", ["independent", "collection"])
def test_native_design_delivery_packing_gate(command_vault_clone, layout):
    root = command_vault_clone.vault_root
    app = application_for(root)
    workspace = app.invoke(WorkspaceCreateRequest("delivery"))
    assert workspace.status == "ok", workspace
    paths = {}
    for kind, title in [("living/design", "Delivery Design"), ("temporal/plan", "Delivery Plan"),
                        ("temporal/shaping-transcript", "Delivery Transcript"),
                        ("temporal/report", "Delivery Evidence")]:
        result = app.invoke(ArtefactCreateRequest(kind, title, content=InlineContent("# " + title + "\n")))
        assert result.status == "ok", result
        paths[kind] = result.result.path
        assert app.invoke(ArtefactReadRequest(result.result.path)).status == "ok"
    probe = PackingProbe(root, layout)
    records = {
        "implements": {"bucket": "plan", "subject": paths["temporal/plan"], "predicate": "implements", "object": paths["living/design"]},
        "confidence": {"bucket": "plan", "subject": paths["temporal/plan"], "predicate": "confidence", "object": 0.9},
        "assessment": {"bucket": "plan", "subject": "implements", "predicate": "assessed-by", "object": paths["temporal/report"]},
        "criterion": {"bucket": "plan", "subject": "assessment", "predicate": "criterion", "object": "completeness"},
        "transcript": {"bucket": "design", "subject": paths["living/design"], "predicate": "transcript", "object": paths["temporal/shaping-transcript"]},
        "annotation": {"bucket": "design", "subject": "native-parent-occurrence", "predicate": "explained-by", "object": paths["temporal/report"]},
    }
    cold = perf_counter()
    assert probe.apply(records, {key: revision(None) for key in records}) == "ok"
    cold_ms = (perf_counter() - cold) * 1000
    warm = []
    for _ in range(9):
        start = perf_counter()
        assert probe.load() == records
        warm.append((perf_counter() - start) * 1000)
    expected = {"confidence": revision(records["confidence"])}
    def setter(value):
        return probe.apply({"confidence": {**records["confidence"], "object": value}}, expected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(setter, [0.8, 0.7]))
    assert sorted(outcomes) == ["conflict", "ok"]
    transition = app.invoke(ArtefactSetStatusRequest(paths["living/design"], "ready"))
    assert transition.status == "ok", transition
    # Same minimal event/correction bytes in each candidate; no capture claim.
    events = [{"id": "transition", "type": "native-transition", "subject": paths["living/design"],
               "before": transition.result.old_value, "after": transition.result.new_value,
               "operation": "fixture-transition", "observed_at": "2026-10-19T00:00:00Z", "contract": "v1"},
              {"id": "correction", "type": "correction", "corrects": "transition", "reason": "Clarify evidence",
               "operation": "fixture-correction", "observed_at": "2026-10-19T00:00:01Z", "contract": "v1"}]
    revised = probe.load()
    revised["assessment"] = {**revised["assessment"], "object": "withdrawn with explicit disposition"}
    assert probe.apply({"assessment": revised["assessment"]}, {"assessment": revision(records["assessment"])}) == "ok"
    assert probe.load() == revised
    print(encoded({"layout": layout, "relationships": len(records), "native_calls": 10,
                   "graph_batches": probe.reconciliations, "files": len(list(probe.directory.glob("*.json"))),
                   "cold_create_ms": round(cold_ms, 3), "warm_load_median_ms": round(median(warm), 3),
                   "written_bytes": probe.written_bytes, "history_events": len(events),
                   "history_bytes": len(encoded(events).encode()), "global_identity_scan": "probe only; excluded from packing attribution"}))


class PublicationReplay:
    """Compare packing of the actual native publication stream, outside authority.

    Native commands always use production independent files. The alternative
    packs the identical admitted records by kind; it is a measured replay, not
    a second implementation of native identity or concurrency semantics.
    """

    def __init__(self, root, layout):
        self.directory = root / '.brain/local/publication-replay' / layout
        self.directory.mkdir(parents=True)
        self.layout, self.records = layout, {}
        self.bytes, self.seconds, self.writes = 0, 0.0, 0

    def read_all(self):
        """Read identical logical records from the measured physical containers."""
        values = {}
        for path in self.directory.glob('*.json'):
            values.update(json.loads(path.read_text()))
        return values

    def container_revision(self, kind, identity):
        """Capture a packing-level stale-writer token, not a native admission."""
        bucket = (kind, identity) if self.layout == 'independent' else (kind,)
        path = self.directory / ('--'.join(bucket) + '.json')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def apply(self, records, removals):
        started = perf_counter()
        for kind, identity in removals:
            self.records.pop((kind, identity), None)
        changed = set()
        for record in records:
            key = (record['kind'], record['id'])
            from _relationships.storage import SCHEMA
            self.records[key] = {**record, 'schema': SCHEMA}
            changed.add(key if self.layout == 'independent' else (record['kind'],))
        changed.update((kind, identity) if self.layout == 'independent' else (kind,) for kind, identity in removals)
        plan = FilePlan()
        for bucket in changed:
            values = {kind + ':' + identity: value for (kind, identity), value in self.records.items()
                      if ((kind, identity) if self.layout == 'independent' else (kind,)) == bucket}
            path = self.directory / ('--'.join(bucket) + '.json')
            if values:
                plan.write_text(path, encoded(values))
            elif path.exists():
                plan.delete(path)
        changes = plan.changes()
        self.bytes += sum(len(change.after or b'') for change in changes)
        self.writes += len(changes)
        apply_file_changes(changes)
        self.seconds += perf_counter() - started


@pytest.mark.parametrize('layout', ['independent', 'collection'])
def test_integrated_native_publication_packing_and_growth(command_vault_clone, monkeypatch, layout):
    from _application.relationship.define import RelationshipDefineRequest
    from _application.relationship.batch import RelationshipBatchRequest
    from _application.relationship.expose import RelationshipExposeRequest
    from _application.relationship.read import RelationshipReadRequest
    from _application.relationship.check import RelationshipCheckRequest
    from _application.relationship._types import DefinitionKind, Action, BatchItem, Disposition
    from _application.history.write import HistoryWriteRequest
    from _application.history.read import HistoryReadRequest
    from _application.history.correct import HistoryCorrectRequest
    from _application.history.policy import HistoryPolicyRequest
    from _application.artefact.set_key import ArtefactSetKeyRequest
    from _relationships.model import Endpoint, NodeKind
    from _relationships.storage import Store
    from _relationships.policies import TranscriptPolicy
    from test_relationship_commands import ok
    root, app = command_vault_clone.vault_root, application_for(command_vault_clone.vault_root)
    replay = PublicationReplay(root, layout)
    original_write, original_reconcile = Store.write, Store.reconcile
    reconciliations, native_calls = 0, 0
    def write(store, records, removals=()):
        result = original_write(store, records, removals)
        replay.apply(records, removals)
        return result
    def reconcile(store, records, removals=()):
        nonlocal reconciliations
        reconciliations += 1
        return original_reconcile(store, records, removals)
    monkeypatch.setattr(Store, 'write', write)
    monkeypatch.setattr(Store, 'reconcile', reconcile)
    def invoke(request):
        nonlocal native_calls
        native_calls += 1
        return ok(app.invoke(request))
    started = perf_counter()
    paths = {}
    for kind, title in [('living/design', 'Measured Design'), ('temporal/plan', 'Measured Plan'),
                        ('temporal/shaping-transcript', 'Measured Transcript'), ('temporal/report', 'Measured Report')]:
        paths[kind] = invoke(ArtefactCreateRequest(kind, title, content=InlineContent('# ' + title + '\n'))).path
    nodes = {kind: Endpoint(NodeKind.ARTEFACT, path) for kind, path in paths.items()}
    for predicate in ('implements', 'confidence', 'criterion', 'assessed-by', 'source', 'transcripts'):
        invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, predicate, predicate, 'Measured ' + predicate))
    design = nodes['living/design']
    occurrence = next(row for row in invoke(RelationshipExposeRequest(design)).records if row['predicate'] == 'native.status')
    items = (
        BatchItem(Action.CREATE, 'implements', nodes['temporal/plan'], 'implements', design),
        BatchItem(Action.CREATE, 'confidence', nodes['temporal/plan'], 'confidence', Endpoint(NodeKind.LITERAL, value=.9)),
        BatchItem(Action.CREATE, 'assessment', Endpoint(NodeKind.RELATIONSHIP, 'implements'), 'assessed-by', nodes['temporal/report']),
        BatchItem(Action.CREATE, 'criterion', Endpoint(NodeKind.RELATIONSHIP, 'assessment'), 'criterion', Endpoint(NodeKind.LITERAL, value='completeness')),
        BatchItem(Action.CREATE, 'transcript', design, 'transcripts', nodes['temporal/shaping-transcript'], scope='confirmed'),
        BatchItem(Action.CREATE, 'source', nodes['temporal/shaping-transcript'], 'source', design, scope='confirmed'),
        BatchItem(Action.CREATE, 'annotation', Endpoint(NodeKind.OCCURRENCE, occurrence['id']), 'assessed-by', nodes['temporal/report']),
    )
    previous = reconciliations
    cold = perf_counter()
    batch = invoke(RelationshipBatchRequest(items))
    cold_create_ms = (perf_counter() - cold) * 1000
    assert reconciliations == previous + 1
    first = perf_counter()
    baseline = invoke(RelationshipReadRequest('implements')).records[0]
    first_read_ms = (perf_counter() - first) * 1000
    warm = []
    for _ in range(5):
        tick = perf_counter()
        assert invoke(RelationshipReadRequest('implements')).records[0]['revision'] == baseline['revision']
        warm.append((perf_counter() - tick) * 1000)
    stale_container = replay.container_revision('assertion', 'implements')
    invoke(ArtefactSetKeyRequest(paths['living/design'], 'measured-renamed'))
    assert replay.container_revision('assertion', 'implements') != stale_container
    design = Endpoint(NodeKind.ARTEFACT, 'design/measured-renamed')
    assert invoke(RelationshipReadRequest('implements')).records[0]['revision'] == baseline['revision']
    annotation = next(row for row in batch.records if row['id'] == 'annotation')
    invoke(ArtefactSetStatusRequest(design.reference, 'ready', relationship_dispositions=(Disposition('relationship:annotation', annotation['revision']),)))
    assert invoke(RelationshipReadRequest('annotation')).records[0]['retired']
    invoke(RelationshipDefineRequest(DefinitionKind.EVENT, 'milestone', 'Milestone', 'Measured caller milestone'))
    event = invoke(HistoryWriteRequest('milestone', design)).records[0]['event']
    invoke(HistoryCorrectRequest(event['id'], event['revision'], 'Correct explanation', 'Revised milestone'))
    assert invoke(HistoryReadRequest(event['id'])).records[0]['interpretation']['state'] == 'corrected'
    policy = TranscriptPolicy((design,), (nodes['temporal/shaping-transcript'],), 'confirmed')
    checked = invoke(RelationshipCheckRequest(transcript=policy))
    assert checked.records[0]['state'] == 'satisfied', checked
    usage = invoke(HistoryPolicyRequest()).records[0]
    end = perf_counter()
    replay_read_started = perf_counter()
    replay_records = replay.read_all()
    replay_read_ms = (perf_counter() - replay_read_started) * 1000
    from _relationships.storage import KINDS
    authoritative = {}
    for kind in KINDS:
        for row in Store(root).rows(kind=kind):
            authoritative[kind + ':' + row['id']] = Store(root).checked(row)
    assert replay_records == authoritative
    def projection(records):
        return sorted((key, encoded(record.get('subject')), record.get('predicate', record.get('type')), encoded(record.get('object')), record.get('retired', False)) for key, record in records.items())
    assert projection(replay_records) == projection(authoritative)
    print(encoded({'integrated_layout_replay': layout, 'native_calls': native_calls,
        'assertions': len(items), 'reconciliations': reconciliations, 'replay_files': len(list(replay.directory.glob('*.json'))),
        'replay_written_bytes': replay.bytes, 'replay_file_writes': replay.writes,
        'replay_ms': round(replay.seconds * 1000, 3), 'native_without_replay_ms': round((end-started-replay.seconds)*1000, 3),
        'cold_create_ms_including_replay': round(cold_create_ms, 3), 'first_read_ms': round(first_read_ms, 3),
        'warm_read_median_ms': round(median(warm), 3), 'history_events': usage['events'], 'history_bytes': usage['serialised_bytes'],
        'operations': len(Store(root).rows(kind='operation')), 'alternative_authority': False, 'packing': 'independent-records' if layout == 'independent' else 'global-per-kind-buckets',
        'replay_read_ms': round(replay_read_ms, 3), 'equivalent_logical_records_and_projection': True, 'stale_container_detected': True}))
