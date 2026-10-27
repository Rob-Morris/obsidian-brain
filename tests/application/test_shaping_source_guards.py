"""Small shaping source controls, separate from shared maintenance fixtures."""
import builtins
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import start_shaping_session as owner
from _application.shaping import _start_preparation as preparation
from _application.shaping.start import ShapingStartRequest, ShapingMode, catalogue_entry
from _application.workspace_transitions import (
    TransitionSnapshot, source_effect_snapshot, transition_effect_snapshot, committed_transition_effects,
)
from _common import PartialApplyError
from _common._source_access import SourceAccessError


NOW = datetime(2026, 10, 27, 10, tzinfo=timezone.utc)
TARGET = 'Wiki/Note.md'
TRANSCRIPT = '_Temporal/Transcripts/Transcript.md'
TEMPLATE = 'Templates/Transcript.md'


def put(root, relative, text='Synthetic content\n'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def observe_opens(monkeypatch, forbidden=()):
    forbidden = {path.resolve() for path in forbidden}
    builtin_open, path_open = builtins.open, Path.open
    opened = []
    def checked(original, path, *args, **kwargs):
        if isinstance(path, (str, Path)):
            path = Path(path)
            assert path.resolve() not in forbidden, 'Prohibited shaping source reached a byte reader'
            opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', lambda path, *a, **kw: checked(builtin_open, path, *a, **kw))
    monkeypatch.setattr(Path, 'open', lambda path, *a, **kw: checked(path_open, path, *a, **kw))
    return opened


@pytest.fixture
def small_session(tmp_path, monkeypatch):
    root = tmp_path / 'vault'
    put(root, TARGET, '---\ntype: living/wiki\n---\n\n# Note\n\nBody.\n')
    put(root, TEMPLATE, '---\ntype: temporal/transcript\n---\n\n**Source:** [[SOURCE_DOC_PATH|SOURCE_DOC_TITLE]]\n')
    artefact = {'key': 'wiki', 'shaping': {'status_behaviour': 'preserve'}}
    transcript = {'key': 'transcripts', 'path': '_Temporal/Transcripts',
                  'frontmatter_type': 'temporal/transcript', 'naming': {'pattern': '{Title}.md'},
                  'template_file': TEMPLATE}
    router = {'artefacts': [artefact, transcript], 'artefact_index': {}}
    monkeypatch.setattr(owner, 'resolve_and_validate_folder', lambda root, router, target: (target, artefact))
    monkeypatch.setattr(owner, '_transcript_artefact', lambda router: transcript)
    monkeypatch.setattr(owner, '_transcript_layout', lambda *a, **kw: ('_Temporal/Transcripts', 'Transcript.md', {}))
    monkeypatch.setattr(owner, '_is_same_day_transcript', lambda *a: True)
    return root, router, transcript


def context(root):
    return SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root),
                           clock=SimpleNamespace(now=lambda: NOW))


def binding_plan(root, router):
    prepared = owner._prepare_shaping_target(root, router, TARGET)
    return {'prepared': prepared, 'transcript_path': TRANSCRIPT,
            'transcript_type': 'temporal/transcript', 'template': 'Synthetic template',
            'target_fields': {}, 'transcript_fields': {}, 'semantic_transition': None,
            'mutation_context': SimpleNamespace(sources=(), review=lambda: {}),
            'transcript_exists': False, 'mode': 'refine', 'lifecycle': None}


@pytest.mark.parametrize('route', ['direct', 'registered'])
@pytest.mark.parametrize('scope', ['private', 'outside'])
def test_target_ancestor_alias_refused_before_open(small_session, tmp_path, monkeypatch, route, scope):
    root, router, _ = small_session
    (root / TARGET).unlink()
    (root / 'Wiki').rmdir()
    source = (put(root, '.brain/private/Note.md') if scope == 'private'
              else put(tmp_path / 'other', 'Note.md'))
    (root / 'Wiki').symlink_to(source.parent, target_is_directory=True)
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        if route == 'direct':
            owner.plan_shaping_session(root, router, TARGET, mode='refine', _now=NOW)
        else:
            import _lifecycle.derived_cache_state as cache
            monkeypatch.setattr(cache, 'require_fresh_compiled_router', lambda root: router)
            catalogue_entry().preparation.prepare(context(root), ShapingStartRequest(TARGET, ShapingMode.REFINE))


def test_template_inference_refused_before_private_open(small_session, monkeypatch):
    root, router, transcript = small_session
    source = put(root, '.brain/private/Template.md')
    alias = root / 'Templates/Chosen.md'
    alias.symlink_to(source)
    transcript['template_file'] = 'Templates/Chosen'
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        owner.plan_shaping_session(root, router, TARGET, mode='refine', _now=NOW)
    assert not (root / TRANSCRIPT).exists()


@pytest.mark.parametrize('template', [TEMPLATE, '.brain-core/artefact-library/temporal/transcripts/template.md'])
def test_custom_and_core_templates_remain_readable(small_session, template):
    root, _, transcript = small_session
    source = put(root, template, 'Permitted template\n')
    transcript['template_file'] = template[:-3]
    assert owner._read_transcript_template(root, transcript) == source.read_text()


@pytest.mark.parametrize('choice', ['linked', 'widest', 'candidate'])
def test_transcript_choices_admit_source_before_read(small_session, monkeypatch, choice):
    root, router, transcript = small_session
    source = put(root, '.brain/private/Transcript.md')
    alias = root / TRANSCRIPT
    alias.parent.mkdir(parents=True)
    alias.symlink_to(source)
    index = {'md_basenames': {'transcript': [TRANSCRIPT], 'note': [TARGET]}}
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        if choice == 'linked':
            owner._linked_transcripts('**Transcripts:** [[_Temporal/Transcripts/Transcript]]',
                                      root, TARGET, index, transcript, {})
        else:
            if choice == 'widest':
                monkeypatch.setattr(owner, '_linked_transcripts', lambda *a: [TRANSCRIPT, '_Temporal/Transcripts/Other.md'])
            owner._choose_transcript_path(root, TARGET, '', 'Note', NOW, index, transcript, router)


def test_create_then_continue_preserves_transcript_flow(small_session):
    root, router, _ = small_session
    first = owner.start_shaping_session(root, router, TARGET, mode='refine', _now=NOW)
    assert first['transcript_operation'] == 'created'
    assert first['status_changed'] is False
    second = owner.start_shaping_session(root, router, TARGET, mode='refine', _now=NOW)
    assert second['transcript_path'] == first['transcript_path']
    assert second['transcript_operation'] == 'appended'
    assert (root / TRANSCRIPT).read_text().count('## Refine session start') == 2


def test_legacy_archived_target_retains_existing_shaping_support(small_session):
    root, router, _ = small_session
    archived = 'Wiki/_Archive/Note.md'
    put(root, archived, (root / TARGET).read_text())
    result = owner.start_shaping_session(root, router, archived, mode='refine', _now=NOW)
    assert result['target_path'] == archived
    assert result['transcript_path'] == TRANSCRIPT
    assert '**Transcripts:**' in (root / archived).read_text()


@pytest.mark.parametrize('subject', ['target', 'transcript', 'parent'])
def test_binding_sources_cannot_use_neutral_hash_authority(small_session, monkeypatch, subject):
    root, router, _ = small_session
    plan = binding_plan(root, router)
    source = put(root, '.brain/private/Hidden.md')
    if subject == 'target':
        alias = root / TARGET
        alias.unlink()
    elif subject == 'transcript':
        alias = root / TRANSCRIPT
        alias.parent.mkdir(parents=True)
    else:
        alias = root / 'Wiki/Parent.md'
        plan['target_fields'] = {'parent': 'wiki/parent'}
        router['artefact_index']['wiki/parent'] = {'path': 'Wiki/Parent.md'}
    alias.symlink_to(source)
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        preparation.session_binding(context(root), ShapingStartRequest(TARGET, ShapingMode.REFINE),
                                    plan=plan, router=router)


def test_missing_new_transcript_observation_remains_allowed(small_session):
    root, router, _ = small_session
    plan = binding_plan(root, router)
    binding = preparation.session_binding(context(root), ShapingStartRequest(TARGET, ShapingMode.REFINE),
                                          plan=plan, router=router)
    observed = next(item for item in binding.observations if item.kind == 'transcript')
    assert observed.revision is None
    assert not (root / TRANSCRIPT).exists()


def test_session_planner_guards_its_existing_transcript_reread(small_session, monkeypatch):
    root, router, _ = small_session
    source = put(root, '.brain/private/Transcript.md')
    alias = root / TRANSCRIPT
    alias.parent.mkdir(parents=True)
    alias.symlink_to(source)
    monkeypatch.setattr(owner, 'plan_shaping_session', lambda *a, **kw: {
        'transcript_exists': True, 'transcript_path': TRANSCRIPT})
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        preparation.session_plan(context(root), ShapingStartRequest(TARGET, ShapingMode.REFINE), router)


def test_execution_append_guard_precedes_any_write(small_session, monkeypatch):
    root, router, _ = small_session
    plan = owner.plan_shaping_session(root, router, TARGET, mode='refine', _now=NOW)
    plan['transcript_exists'] = True
    source = put(root, '.brain/private/Transcript.md')
    alias = root / TRANSCRIPT
    alias.parent.mkdir(parents=True)
    alias.symlink_to(source)
    observe_opens(monkeypatch, [source])
    monkeypatch.setattr(owner, 'safe_write_artefact', lambda *a, **kw: pytest.fail('Write preceded refused source'))
    with pytest.raises(SourceAccessError):
        owner.start_shaping_session(root, router, TARGET, mode='refine', _plan=plan)


def test_late_refusal_keeps_direct_lifecycle_partial_semantics(small_session, monkeypatch):
    root, router, _ = small_session
    plan = owner.plan_shaping_session(root, router, TARGET, mode='refine', _now=NOW)
    plan.update(status_changed=True, lifecycle=object())
    source = put(root, '.brain/private/Hidden.md')
    alias = root / 'Wiki/Redirected.md'
    alias.symlink_to(source)
    def apply(root_path, lifecycle):
        (root / TARGET).write_text('Durable lifecycle change\n')
        return {'path': 'Wiki/Redirected.md'}
    monkeypatch.setattr(owner.edit, 'apply_artefact_transition', apply)
    import _portable.transition_indexes as indexes
    monkeypatch.setattr(indexes, 'reconcile_artefact_indexes', lambda root: None)
    observe_opens(monkeypatch, [source])
    with pytest.raises(PartialApplyError, match='partially applied'):
        owner.start_shaping_session(root, router, TARGET, mode='refine', _plan=plan)
    assert (root / TARGET).read_text() == 'Durable lifecycle change\n'


def lifecycle_plan(path):
    return SimpleNamespace(writes=({'path': path},),
        movement=SimpleNamespace(links=SimpleNamespace(writes=()), moves=()))


def test_shared_snapshot_refuses_transition_alias_before_byte_read(small_session, monkeypatch):
    root, _, _ = small_session
    source = put(root, '.brain/private/Hidden.md')
    alias = root / 'Wiki/Changed.md'
    alias.symlink_to(source)
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        transition_effect_snapshot(context(root), lifecycle_plan('Wiki/Changed.md'))


def test_shared_snapshot_preserves_canonical_alias_conversion_and_missing_endpoint(small_session, monkeypatch):
    from _common import document_revision
    root, _, _ = small_session
    raw = 'Encoded body\n'.encode('utf-16')
    source = root / 'Wiki/Encoded.md'
    source.write_bytes(raw)
    alias = root / 'Wiki/Alias.md'
    alias.symlink_to(source)
    opened = observe_opens(monkeypatch)
    snapshot = source_effect_snapshot(context(root), ['Wiki/Alias.md', 'Wiki/New.md'], text_writes=['Wiki/Alias.md'])
    assert snapshot.revisions == {'Wiki/Alias.md': document_revision(raw), 'Wiki/New.md': None}
    assert snapshot.conversions and snapshot.conversions[0][0] == 'Wiki/Alias.md'
    assert source in opened and alias not in opened


@pytest.mark.parametrize('family', ['transition', 'session'])
def test_post_effect_observation_refusal_is_uncertain_not_request_error(small_session, monkeypatch, family):
    root, router, _ = small_session
    plan = binding_plan(root, router)
    source = put(root, '.brain/private/Hidden.md')
    alias = root / TARGET
    alias.unlink()
    alias.symlink_to(source)
    observe_opens(monkeypatch, [source])
    request = ShapingStartRequest(TARGET, ShapingMode.REFINE)
    with pytest.raises(RuntimeError, match='could not be observed safely') as caught:
        if family == 'transition':
            committed_transition_effects(context(root), request, lifecycle_plan(TARGET), TransitionSnapshot({TARGET: 'before'}, ()))
        else:
            preparation.committed_session_effects(context(root), request, plan, TransitionSnapshot({TARGET: 'before'}, ()))
    assert not isinstance(caught.value, (ValueError, OSError))


def test_semantic_transition_guards_existing_transcript_read(small_session, monkeypatch):
    root, router, _ = small_session
    plan = binding_plan(root, router)
    plan.update(transcript_exists=True, now=NOW)
    source = put(root, '.brain/private/Transcript.md')
    alias = root / TRANSCRIPT
    alias.parent.mkdir(parents=True)
    alias.symlink_to(source)
    observe_opens(monkeypatch, [source])
    with pytest.raises(SourceAccessError):
        preparation.semantic_transition(context(root), router, plan)


def test_session_snapshot_reuses_lifecycle_source_without_second_read(small_session, monkeypatch):
    root, router, _ = small_session
    plan = binding_plan(root, router)
    plan['lifecycle'] = lifecycle_plan(TARGET)
    opened = observe_opens(monkeypatch)
    snapshot = preparation.session_effect_snapshot(context(root), plan)
    assert snapshot.revisions[TARGET]
    assert snapshot.revisions[TRANSCRIPT] is None
    assert opened.count(root / TARGET) == 1


def test_shared_workspace_preparation_admits_subject_before_policy_read(small_session, monkeypatch):
    from _application.workspace_transitions import prepare_workspace_transition
    root, _, _ = small_session
    source = put(root, '.brain/private/Hidden.md')
    alias = root / 'Wiki/Changed.md'
    alias.symlink_to(source)
    observe_opens(monkeypatch, [source])
    request = SimpleNamespace(COMMAND_ID='artefact.archive')
    plan = SimpleNamespace(writes=({'path': 'Wiki/Changed.md'},))
    with pytest.raises(SourceAccessError):
        prepare_workspace_transition(context(root), request, {}, plan)
