"""Scoped current-standing policy consumes native persisted revisions."""

from dataclasses import replace
import pytest
from _application.relationship.batch import RelationshipBatchRequest
from _application.relationship.define import RelationshipDefineRequest
from _application.relationship.check import RelationshipCheckRequest
from _application.relationship._types import Action, BatchItem, DefinitionKind
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
from _application._mutation_support import InlineContent
from _application.artefact.archive import ArtefactArchiveRequest
from _relationships.model import Endpoint, NodeKind
from _relationships.policies import TranscriptPolicy
from _relationships.storage import Store
from _common import document_revision_at
from test_relationship_compatibility import graph, assessed, ok, read, SUBJECT


def test_conflicting_current_claims_remain_independent_without_native_status_changes(assessed):
    root, app, policy = assessed
    store = Store(root)
    ids = ('claim', 'criterion', 'verdict', 'attribution', 'evidence', 'subject-pin', 'criterion-pin', 'evidence-pin', 'definition-pins')
    def endpoint(node):
        reference = node['reference']
        if node['kind'] == 'relationship' and reference in ids:
            reference += '-second'
        return Endpoint(NodeKind(node['kind']), reference, node.get('value'))
    items = []
    for identity in ids:
        record = store.read('assertion', identity)
        obj = Endpoint(NodeKind.LITERAL, value='rejected') if identity == 'verdict' else endpoint(record['object'])
        subject = SUBJECT if identity == 'claim' else endpoint(record['subject'])
        items.append(BatchItem(Action.CREATE, identity+'-second', subject, record['predicate'], obj, scope=policy.scope))
    before = {path: path.read_bytes() for path in root.glob('Designs/**/*.md')}
    ok(app.invoke(RelationshipBatchRequest(tuple(items))))
    result = ok(app.invoke(RelationshipCheckRequest(assessment=policy)))
    assert result.records[0]['state'] == 'conflicting', result
    assert len(result.records[0]['claims']) == 2
    assert {path: path.read_bytes() for path in before} == before
    assert ok(app.invoke(RelationshipCheckRequest())).records == ()


@pytest.mark.parametrize('change', ['body', 'archive'])
def test_persisted_evidence_change_or_archive_cannot_satisfy_policy(assessed, change):
    root, app, policy = assessed
    evidence = read(app, 'evidence')['object']['reference']
    if change == 'body':
        ok(app.invoke(DocumentWriteBodyRequest(DocumentLocator(DocumentResource.ARTEFACT, evidence),
            document_revision_at(root/evidence), DocumentWriteBodyOperation.APPEND, InlineContent('\nUpdated evidence.\n'))))
    else:
        ok(app.invoke(ArtefactArchiveRequest(evidence)))
    outcome = ok(app.invoke(RelationshipCheckRequest(assessment=policy))).records[0]
    assert outcome['state'] == 'unknown', outcome


def test_transcript_reciprocity_is_only_an_explicit_selected_obligation(graph):
    from command_vault import load_command_vault_manifest
    root, app = graph
    source = Endpoint(NodeKind.ARTEFACT, load_command_vault_manifest()['stable_artefacts']['owned_plan']['path'])
    transcript = SUBJECT
    policy = TranscriptPolicy((source,), (transcript,), 'confirmed')
    assert ok(app.invoke(RelationshipCheckRequest())).records == ()
    assert ok(app.invoke(RelationshipCheckRequest(transcript=policy))).records[0]['state'] == 'missing'
    for predicate in ('source', 'transcripts'):
        ok(app.invoke(RelationshipDefineRequest(DefinitionKind.PREDICATE, predicate, predicate, 'Explicit persisted ' + predicate)))
    ok(app.invoke(RelationshipBatchRequest((
        BatchItem(Action.CREATE, 'source-link', transcript, 'source', source, scope='confirmed'),
        BatchItem(Action.CREATE, 'transcript-link', source, 'transcripts', transcript, scope='confirmed'),
    ))))
    assert ok(app.invoke(RelationshipCheckRequest(transcript=policy))).records[0]['state'] == 'satisfied'
    assert ok(app.invoke(RelationshipCheckRequest(transcript=replace(policy, scope='different')))).records[0]['state'] == 'missing'
    bounded = ok(app.invoke(RelationshipCheckRequest(work_budget=2, transcript=policy)))
    assert not bounded.complete and bounded.visited <= 2
    assert bounded.records[0]['state'] != 'satisfied'


def test_scoped_and_paged_checks_never_promote_partial_policy_inputs(assessed):
    root, app, policy = assessed
    scoped = ok(app.invoke(RelationshipCheckRequest(assessment=policy, identities=('relationship:claim',))))
    assert scoped.records[0]['state'] != 'satisfied'
    store = Store(root)
    store.write([{'kind': 'event', 'id': 'retired-page-' + str(index), 'retired': True, 'revision': 'historic'} for index in range(110)])
    cursor = None
    for _ in range(5):
        page = ok(app.invoke(RelationshipCheckRequest(assessment=policy, cursor=cursor)))
        assert page.records[0]['state'] != 'satisfied'
        cursor = page.continuation
        if cursor is None:
            break
    assert cursor is None
