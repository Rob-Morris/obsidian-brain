"""Native effect boundaries retain branch continuity independently of adapters."""

from hashlib import sha256
from types import SimpleNamespace

import pytest

from _bootstrap import branch_guard
import create
import define
import edit
import shape_printable
import sync_definitions
import upload_attachment
from _lifecycle import text_repair_io
from _skill_library import service as skills


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    local = tmp_path / '.brain' / 'local'
    local.mkdir(parents=True)
    (local / 'config.yaml').write_text(
        'branch_guard:\n  allowed_branches:\n    - dev/a\n    - dev/b\n', encoding='utf-8'
    )
    state = {'branch': 'dev/a'}
    monkeypatch.setattr(
        branch_guard, 'observe_git',
        lambda root: branch_guard.GitState(str(tmp_path), state['branch']),
    )
    return tmp_path, state


def test_attachment_refuses_before_creating_destination(guarded):
    root, state = guarded
    state['branch'] = 'main'
    with pytest.raises(branch_guard.BranchGuardError):
        upload_attachment.upload_attachment(
            root, None, destination_key='diagrams', name='diagram.svg', content=b'<svg/>'
        )
    assert not (root / '_Assets').exists()


def test_identical_attachment_retry_is_observational_on_blocked_branch(guarded):
    root, state = guarded
    args = dict(destination_key='diagrams', name='diagram.svg', content=b'<svg/>')
    first = upload_attachment.upload_attachment(root, None, **args)
    state['branch'] = 'main'
    retry = upload_attachment.upload_attachment(root, None, **args)
    assert retry == {**first, 'created': False}
    assert (root / first['path']).read_bytes() == b'<svg/>'


def test_attachment_apply_reuses_outer_operation(guarded):
    root, state = guarded
    plan = upload_attachment.plan_attachment_upload(
        root, None, destination_key='diagrams', name='diagram.svg', content=b'<svg/>'
    )
    with branch_guard.operation(root):
        state['branch'] = 'dev/b'
        with pytest.raises(branch_guard.BranchGuardError):
            upload_attachment.apply_attachment_upload(root, plan)
    assert not plan.target.exists()


def test_definition_branch_loss_keeps_first_write_without_rollback(guarded, monkeypatch):
    root, state = guarded
    config = root / '_Config'
    config.mkdir()
    first = config / 'first.md'
    first.write_text('before', encoding='utf-8')
    plan = define.DefinitionPlan((
        define.DefinitionWrite('_Config/first.md', 'after', before_content='before', exclusive=False),
        define.DefinitionWrite('_Config/second.md', 'second', before_content=None, exclusive=True),
    ), {})
    original = define.safe_write

    def switch_after_write(*args, **kwargs):
        original(*args, **kwargs)
        state['branch'] = 'dev/b'

    monkeypatch.setattr(define, 'safe_write', switch_after_write)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        define.apply_definition_plan(root, plan)
    assert first.read_text(encoding='utf-8') == 'after'
    assert not (config / 'second.md').exists()
    assert caught.value.completed_effects == (
        branch_guard.BranchGuardError.CompletedEffect('write', '_Config/first.md'),)


def test_metadata_batch_stops_before_second_write(guarded, monkeypatch):
    root, state = guarded
    notes = root / 'Notes'
    notes.mkdir()
    for name in ('first.md', 'second.md'):
        (notes / name).write_text('before', encoding='utf-8')
    original = edit.safe_write_active_or_archived_artefact

    def switch_after_write(*args, **kwargs):
        original(*args, **kwargs)
        state['branch'] = 'dev/b'

    monkeypatch.setattr(edit, 'safe_write_active_or_archived_artefact', switch_after_write)
    operations = [dict(path=f'Notes/{name}.md', fields={}, body='after')
                  for name in ('first', 'second')]
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        edit._write_frontmatter_mutations(root, operations, operation='repair')
    assert 'after' in (notes / 'first.md').read_text(encoding='utf-8')
    assert (notes / 'second.md').read_text(encoding='utf-8') == 'before'
    assert caught.value.completed_effects == (
        branch_guard.BranchGuardError.CompletedEffect('write', 'Notes/first.md'),)


def test_definition_sync_dry_run_does_not_gate_or_write(guarded):
    root, state = guarded
    state['branch'] = 'main'
    plan = SimpleNamespace(result={'dry_run': True})
    assert sync_definitions.apply_definition_sync(root, plan) == {'dry_run': True}
    assert not (root / '_Config').exists()


def test_definition_copy_refuses_before_parent_creation(guarded):
    root, state = guarded
    state['branch'] = 'main'
    copy = SimpleNamespace(target='_Config/Types/note/taxonomy.md', content=b'body')
    with pytest.raises(branch_guard.BranchGuardError):
        sync_definitions._apply_definition_copies(root, [copy])
    assert not (root / '_Config').exists()


def test_named_resource_refuses_before_write(guarded):
    root, state = guarded
    state['branch'] = 'main'
    plan = SimpleNamespace(path='_Config/Memories/example.md', content='body',
                           exclusive=True, resource='memory', name='example')
    with pytest.raises(branch_guard.BranchGuardError):
        create.apply_named_resource_creation(root, plan)
    assert not (root / '_Config').exists()


@pytest.mark.parametrize('created', [False, True])
def test_shaping_existing_markdown_pdf_regeneration_remains_allowed(
    guarded, monkeypatch, created
):
    root, state = guarded
    state['branch'] = 'main'
    plan = SimpleNamespace(path='_Temporal/Printables/example.md', created=created,
                           content='body')
    if created:
        with pytest.raises(branch_guard.BranchGuardError):
            shape_printable.shape(root, {'render': False}, _plan=plan)
        assert not (root / plan.path).exists()
    else:
        target = root / plan.path
        target.parent.mkdir(parents=True)
        target.write_text('existing', encoding='utf-8')
        rendered = []
        monkeypatch.setattr(shape_printable, '_render_pdf',
                            lambda *args: rendered.append(args) or {'rendered': True})
        result = shape_printable.shape(root, {}, _plan=plan)
        assert result['rendered'] is True
        assert len(rendered) == 1
        assert target.read_text(encoding='utf-8') == 'existing'


def test_text_repair_refuses_native_replace_and_cleans_stage(guarded):
    root, state = guarded
    state['branch'] = 'main'
    target = root / 'Notes' / 'example.md'
    target.parent.mkdir()
    target.write_bytes(b'original')
    with pytest.raises(branch_guard.BranchGuardError):
        text_repair_io.repair_candidate(root, 'Notes/example.md',
                                       sha256(b'original').hexdigest(), b'fixed')
    assert target.read_bytes() == b'original'
    assert list(target.parent.iterdir()) == [target]


def test_skill_rollback_refuses_even_after_branch_is_restored(guarded):
    root, state = guarded
    destination = root / '_Config' / 'Skills' / 'example'
    destination.mkdir(parents=True)
    (destination / 'SKILL.md').write_text('installed', encoding='utf-8')
    with branch_guard.operation(root):
        state['branch'] = 'dev/b'
        with pytest.raises(branch_guard.BranchGuardError):
            branch_guard.checkpoint(root)
        state['branch'] = 'dev/a'
        with pytest.raises(branch_guard.BranchGuardError):
            skills._rollback_install(root, destination, None, False)
    assert (destination / 'SKILL.md').read_text(encoding='utf-8') == 'installed'


def test_skill_install_branch_loss_retains_displaced_package_and_stage(guarded, monkeypatch):
    root, state = guarded
    destination = root / '_Config' / 'Skills' / 'example'
    destination.mkdir(parents=True)
    (destination / 'SKILL.md').write_text('original', encoding='utf-8')

    def copy_package(snapshot, target):
        target.mkdir()
        (target / 'SKILL.md').write_text('replacement', encoding='utf-8')

    original_replace = skills.os.replace

    def switch_after_displacement(source, target):
        original_replace(source, target)
        if source == destination:
            state['branch'] = 'dev/b'

    monkeypatch.setattr(skills, 'copy_package', copy_package)
    monkeypatch.setattr(skills.os, 'replace', switch_after_displacement)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        skills._install_snapshot(root, SimpleNamespace(name='example'),
                                 replace=True, archive_existing=False)
    facts = caught.value.facts
    assert not destination.exists()
    assert (root / facts['retained_backup'] / 'SKILL.md').read_text(encoding='utf-8') == 'original'
    assert (root / facts['retained_stage'] / 'SKILL.md').read_text(encoding='utf-8') == 'replacement'


def test_definition_ordinary_failure_rolls_back_on_same_branch(guarded, monkeypatch):
    root, _state = guarded
    config = root / '_Config'
    config.mkdir()
    first = config / 'first.md'
    first.write_text('before', encoding='utf-8')
    plan = define.DefinitionPlan((
        define.DefinitionWrite('_Config/first.md', 'after', before_content='before', exclusive=False),
        define.DefinitionWrite('_Config/second.md', 'second', before_content=None, exclusive=True),
    ), {})
    original = define.safe_write

    def fail_second(path, content, **kwargs):
        if str(path).endswith('second.md'):
            raise OSError('injected failure')
        return original(path, content, **kwargs)

    monkeypatch.setattr(define, 'safe_write', fail_second)
    with pytest.raises(OSError, match='injected failure'):
        define.apply_definition_plan(root, plan)
    assert first.read_text(encoding='utf-8') == 'before'
    assert not (config / 'second.md').exists()


def test_move_batch_stops_on_allowed_to_allowed_branch_switch(guarded, monkeypatch):
    import rename

    root, state = guarded
    notes = root / 'Notes'
    notes.mkdir()
    moves = []
    for name in ('first', 'second'):
        (notes / f'{name}.md').write_text(name, encoding='utf-8')
        moves.append(dict(source=f'Notes/{name}.md', dest=f'Notes/{name}-moved.md'))
    plan = rename.plan_move_and_links(root, moves)
    original = rename.os.rename

    def switch_after_move(source, destination):
        original(source, destination)
        state['branch'] = 'dev/b'

    monkeypatch.setattr(rename.os, 'rename', switch_after_move)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        rename.apply_move_and_links(root, plan)
    applied = caught.value.facts['applied_moves']
    assert len(applied) == 1
    for move in moves:
        assert (root / move['dest']).exists() == (move in applied)
        assert (root / move['source']).exists() == (move not in applied)


def test_displaced_conflict_cleanup_refuses_before_snapshot_removal(guarded):
    root, state = guarded
    previous = root / skills.CONFLICTS_REL / '.example.previous-test'
    previous.mkdir(parents=True)
    (previous / 'recovery.md').write_text('snapshot', encoding='utf-8')
    state['branch'] = 'main'
    with pytest.raises(branch_guard.BranchGuardError):
        skills._cleanup_displaced_conflict(root, previous)
    assert (previous / 'recovery.md').read_text(encoding='utf-8') == 'snapshot'


def test_backup_pruning_rechecks_between_package_removals(guarded, monkeypatch):
    root, state = guarded
    backup_root = root / skills.BACKUPS_REL
    for number in range(3):
        package = backup_root / f'example.previous-{number}'
        package.mkdir(parents=True)
        (package / 'SKILL.md').write_text(str(number), encoding='utf-8')
    monkeypatch.setattr(skills, 'MAX_BACKUPS_PER_SKILL', 1)
    original = skills.shutil.rmtree

    def switch_after_removal(path, *args, **kwargs):
        original(path, *args, **kwargs)
        state['branch'] = 'dev/b'

    monkeypatch.setattr(skills.shutil, 'rmtree', switch_after_removal)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        skills._trim_backups(root, 'example')
    assert len(caught.value.facts['deleted_backup_paths']) == 1
    assert len(list(backup_root.iterdir())) == 2


def test_conflict_staging_refuses_before_recovery_directory_creation(guarded):
    root, state = guarded
    state['branch'] = 'main'
    with pytest.raises(branch_guard.BranchGuardError):
        skills._stage_conflict(root, None, {}, None, 'commit')
    assert not (root / skills.CONFLICTS_REL).exists()


def test_conflict_displacement_retains_snapshot_on_branch_loss(guarded, monkeypatch):
    root, state = guarded
    destination = root / skills.CONFLICTS_REL / 'example'
    destination.mkdir(parents=True)
    (destination / 'recovery.md').write_text('previous', encoding='utf-8')

    def copy_package(snapshot, target):
        target.mkdir()
        (target / 'SKILL.md').write_text('candidate', encoding='utf-8')

    monkeypatch.setattr(skills, 'copy_package', copy_package)
    monkeypatch.setattr(skills, '_changed_manifest_paths', lambda *args: [])
    monkeypatch.setattr(skills, '_snapshot_difference_paths', lambda *args: [])
    original = skills.os.replace

    def switch_after_displacement(source, target):
        original(source, target)
        if source == destination:
            state['branch'] = 'dev/b'

    monkeypatch.setattr(skills.os, 'replace', switch_after_displacement)
    snapshot = SimpleNamespace(name='example', package_sha256='digest')
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        skills._stage_conflict(root, snapshot,
                               {'installed_baseline_sha256': 'baseline', 'installed_manifest': []},
                               snapshot, 'commit')
    facts = caught.value.facts
    assert not destination.exists()
    previous = root / facts['retained_previous_conflict']
    assert (previous / 'recovery.md').read_text(encoding='utf-8') == 'previous'
    assert (root / facts['retained_stage'] / 'upstream' / 'SKILL.md').exists()
