"""Text admission at the link rewrite boundary, with real persisted bytes."""
import sys

import pytest

import fix_links
import rename
from _common._artefacts import scan_artefact_key_reference_index
from _common._wikilinks import (
    apply_wikilink_rewrites, build_wikilink_pattern, plan_wikilink_rewrites,
)


@pytest.fixture
def link_vault(tmp_path):
    core = tmp_path / '.brain-core'
    core.mkdir()
    (core / 'VERSION').write_text('0.70.10\n')
    folder = tmp_path / 'Wiki'
    folder.mkdir()
    (folder / 'Old.md').write_text('---\ntype: living/wiki\nkey: old\n---\nOriginal\n')
    (folder / 'Backlink.md').write_text('See [[Wiki/Old]].\n')
    return tmp_path


def bad_files(root):
    (root / 'Wiki/Bad-one.md').write_bytes(b'broken\xe2\x82')
    (root / 'Wiki/Bad-two.md').write_bytes(b'legacy\x80 text')


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()
            and p.relative_to(root).as_posix() != '.brain/local/mutation.lock'}


def assert_both(exc):
    assert 'Wiki/Bad-one.md' in str(exc.value)
    assert 'Wiki/Bad-two.md' in str(exc.value)


def test_rename_refuses_all_blockers_before_effects(link_vault):
    bad_files(link_vault)
    before = snapshot(link_vault)
    with pytest.raises(ValueError) as exc:
        rename.rename_and_update_links(str(link_vault), 'Wiki/Old.md', 'Wiki/New.md')
    assert_both(exc)
    assert snapshot(link_vault) == before


def test_fix_links_refuses_all_blockers_before_effects(link_vault):
    bad_files(link_vault)
    before = snapshot(link_vault)
    with pytest.raises(ValueError) as exc:
        fix_links.plan_link_fixes(str(link_vault), router={})
    assert_both(exc)
    assert snapshot(link_vault) == before


def test_key_reference_index_refuses_all_blockers(link_vault):
    bad_files(link_vault)
    router = {'artefacts': [{'key': 'wikis', 'type': 'living/wiki',
                             'classification': 'living', 'configured': True, 'path': 'Wiki'}]}
    with pytest.raises(ValueError) as exc:
        scan_artefact_key_reference_index(str(link_vault), router)
    assert_both(exc)


def test_lossless_backlink_conversion_preserves_content_and_reports_only_writes(link_vault):
    text = 'Before café\nSee [[Wiki/Old#Heading|Old]].\nAfter Ω\n'
    (link_vault / 'Wiki/Backlink.md').write_bytes(text.encode('utf-16'))
    untouched = 'Unmatched café\n'.encode('utf-16')
    (link_vault / 'Wiki/Unmatched.md').write_bytes(untouched)
    plan = plan_wikilink_rewrites(str(link_vault), build_wikilink_pattern('Wiki/Old'),
                                 lambda m: m.group(0).replace('Wiki/Old', 'Wiki/New'))
    assert plan.unreadable == ()
    assert [path for path, code in plan.conversions] == ['Wiki/Backlink.md']
    assert plan.conversions[0][1]
    apply_wikilink_rewrites(str(link_vault), plan)
    assert (link_vault / 'Wiki/Backlink.md').read_text() == text.replace('Wiki/Old', 'Wiki/New')
    assert (link_vault / 'Wiki/Unmatched.md').read_bytes() == untouched


def test_direct_fix_cli_has_no_effects(link_vault, monkeypatch, capsys):
    bad_files(link_vault)
    before = snapshot(link_vault)
    monkeypatch.setattr(fix_links, 'load_fresh_compiled_router', lambda root: {})
    monkeypatch.setattr(sys, 'argv', ['fix_links.py', '--fix', '--vault', str(link_vault)])
    with pytest.raises(SystemExit) as exc:
        fix_links.main()
    assert exc.value.code == 1
    output = capsys.readouterr().err
    assert 'Bad-one.md' in output and 'Bad-two.md' in output
    assert snapshot(link_vault) == before


def test_rename_validation_does_not_treat_bad_text_as_empty_frontmatter(link_vault):
    (link_vault / 'Wiki/Old.md').write_bytes(b'truncated\xe2\x82')
    router = {'artefacts': [{'key': 'wikis', 'type': 'living/wiki',
                             'classification': 'living', 'configured': True, 'path': 'Wiki',
                             'naming': {'pattern': '{Title}.md'}}]}
    with pytest.raises(ValueError) as exc:
        rename.validate_rename_request(str(link_vault), 'Wiki/Old.md', 'Wiki/New.md', router)
    assert 'Old.md' in str(exc.value)
    assert 'naming contract' not in str(exc.value)


def test_direct_rename_cli_has_no_effects(link_vault, monkeypatch, capsys):
    bad_files(link_vault)
    before = snapshot(link_vault)
    monkeypatch.setattr(rename, 'load_fresh_compiled_router', lambda root: {})
    with pytest.raises(SystemExit) as exc:
        rename.main(['Wiki/Old.md', 'Wiki/New.md', '--vault', str(link_vault)])
    assert exc.value.code == 1
    output = capsys.readouterr().err
    assert 'Bad-one.md' in output and 'Bad-two.md' in output
    assert snapshot(link_vault) == before


def test_lossless_fix_scan_and_apply(link_vault):
    (link_vault / 'Wiki/Brain Inbox.md').write_text('Target\n')
    text = 'café [[brain-inbox]] Ω\n'
    (link_vault / 'Wiki/Backlink.md').write_bytes(text.encode('utf-16'))
    # Case-insensitive filename resolution supplies the canonical target.
    plan = fix_links.plan_link_fixes(str(link_vault), router={})
    assert plan.rewrites.conversions
    fix_links.apply_link_fix_plan(str(link_vault), plan)
    content = (link_vault / 'Wiki/Backlink.md').read_text()
    assert 'café ' in content and ' Ω\n' in content
    assert '[[Brain Inbox]]' in content


def test_fix_preparation_pins_raw_revision(link_vault):
    from types import SimpleNamespace
    from _application.links.fix import LinksFixRequest, fix_binding
    from _common._document_revision import document_revision
    (link_vault / 'Wiki/Brain Inbox.md').write_text('Target\n')
    raw = 'café [[brain-inbox]] Ω\n'.encode('utf-16')
    (link_vault / 'Wiki/Backlink.md').write_bytes(raw)
    plan = fix_links.plan_link_fixes(str(link_vault), router={})
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=link_vault))
    binding = fix_binding(context, LinksFixRequest(), plan=plan)
    assert any(item.kind == 'source' and item.identity == 'Wiki/Backlink.md'
               and item.revision == document_revision(raw) for item in binding.observations)


def test_key_change_reference_planner_names_all_blockers(link_vault):
    import edit
    bad_files(link_vault)
    router = {'artefacts': [{'key': 'wikis', 'type': 'living/wiki',
                             'classification': 'living', 'configured': True, 'path': 'Wiki'}]}
    before = snapshot(link_vault)
    with pytest.raises(ValueError) as exc:
        edit._plan_reference_mutation(str(link_vault), router, 'wiki/old', 'wiki/new',
                                      operation='key-change')
    assert_both(exc)
    assert snapshot(link_vault) == before


def test_rename_planner_includes_bad_source_and_other_blockers(link_vault):
    bad_files(link_vault)
    (link_vault / 'Wiki/Old.md').write_bytes(b'truncated\xe2\x82')
    router = {'artefacts': [{'key': 'wikis', 'type': 'living/wiki',
                             'classification': 'living', 'configured': True, 'path': 'Wiki',
                             'naming': {'pattern': '{Title}.md'}}]}
    with pytest.raises(ValueError) as exc:
        rename.rename_and_update_links(str(link_vault), 'Wiki/Old.md',
                                       'Wiki/New.md', router=router)
    assert_both(exc)
    assert 'Wiki/Old.md' in str(exc.value)


def test_transition_preparation_parses_lossless_text_and_pins_raw_revision(link_vault):
    from types import SimpleNamespace
    from _application.artefact.rename import ArtefactRenameRequest
    from _application.preparation_transition import transition_binding
    from _common._document_revision import document_revision
    raw = '---\ntype: living/wiki\nkey: old\n---\nCafé Ω\n'.encode('utf-16')
    (link_vault / 'Wiki/Old.md').write_bytes(raw)
    plan = rename.plan_move_and_links(str(link_vault),
        [{'source': 'Wiki/Old.md', 'dest': 'Wiki/New.md'}])
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=link_vault))
    router = {'artefacts': [{'key': 'wikis', 'type': 'living/wiki'}]}
    binding = transition_binding(context,
        ArtefactRenameRequest('Wiki/Old.md', 'Wiki/New.md'), plan=plan, router=router)
    assert any(item.kind == 'source' and item.identity == 'Wiki/Old.md'
               and item.revision == document_revision(raw) for item in binding.observations)
    assert any(item.kind == 'definition' and item.identity == 'wikis'
               for item in binding.observations)


@pytest.fixture
def reparent_vault(link_vault):
    from _common import parse_frontmatter
    from _common._artefacts import living_artefact_index_entry, finalize_living_artefact_index
    art = {'key': 'wikis', 'type': 'living/wiki', 'frontmatter_type': 'living/wiki',
           'classification': 'living', 'configured': True, 'path': 'Wiki',
           'naming': {'pattern': '{Title}.md'}}
    child = link_vault / 'Wiki/wiki~old/Child.md'
    child.parent.mkdir()
    child.write_text('---\ntype: living/wiki\nkey: child\nparent: wiki/old\n'
                     'tags: [wiki/old]\n---\nChild\n')
    entries = {}
    for path in ('Wiki/Old.md', 'Wiki/wiki~old/Child.md'):
        fields, _ = parse_frontmatter((link_vault / path).read_text())
        entries['wiki/' + fields['key']] = living_artefact_index_entry(art, path, fields)
    router = {'artefacts': [art], 'artefact_index': finalize_living_artefact_index(entries)}
    return link_vault, router


def test_reparent_reuses_scan_reads_and_keeps_raw_backlink_pins(reparent_vault, monkeypatch):
    from collections import Counter
    from pathlib import Path
    from types import SimpleNamespace
    import edit
    from _common._document_revision import document_revision
    from _application.artefact.reparent import ArtefactReparentRequest
    from _application.preparation_transition import transition_binding
    root, router = reparent_vault
    raw = 'Café [[Wiki/wiki~old/Child#Heading|child]] Ω\n'.encode('utf-16')
    (root / 'Wiki/Backlink.md').write_bytes(raw)
    for i in range(50):
        (root / f'Wiki/Unrelated-{i}.md').write_text('Unrelated\n')
    before = snapshot(root)
    reads = Counter()
    import builtins
    from _common import _artefacts
    original = builtins.open

    def counted(path, mode, *args, **kwargs):
        if mode == "rb":
            reads[Path(path).relative_to(root).as_posix()] += 1
        return original(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(_artefacts, "open", counted, raising=False)
        plan = edit.plan_reparent_children(str(root), router, 'Wiki/Old.md',
                                           None, to_provided=True)
    assert all(reads[f'Wiki/Unrelated-{i}.md'] == 1 for i in range(50))
    assert reads['Wiki/Backlink.md'] == 1
    assert reads['Wiki/wiki~old/Child.md'] == 1
    write = next(w for w in plan.movement.links.writes if w.path == 'Wiki/Backlink.md')
    assert write.before.revision == document_revision(raw)
    assert write.conversion_code
    assert write.after == 'Café [[Wiki/Child#Heading|child]] Ω\n'
    assert ('Wiki/Backlink.md', write.conversion_code) in plan.movement.links.conversions
    binding = transition_binding(SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root)),
        ArtefactReparentRequest('Wiki/Old.md', None), plan=plan, router=router)
    assert any(item.identity == 'Wiki/Backlink.md' and item.kind == 'source'
               and item.revision == document_revision(raw) for item in binding.observations)
    assert snapshot(root) == before
    changed = 'Fresh [[Wiki/wiki~old/Child]]\n'.encode('utf-8')
    (root / 'Wiki/Backlink.md').write_bytes(changed)
    fresh = edit.plan_reparent_children(str(root), router, 'Wiki/Old.md', None, to_provided=True)
    fresh_write = next(w for w in fresh.movement.links.writes if w.path == 'Wiki/Backlink.md')
    assert fresh_write.before.revision == document_revision(changed)
    assert fresh_write.conversion_code is None
    assert fresh_write.after == 'Fresh [[Wiki/Child]]\n'


def test_reparent_collects_mixed_root_and_encoded_child_blockers_before_effects(reparent_vault):
    import edit
    root, router = reparent_vault
    child = root / 'Wiki/wiki~old/Child.md'
    child.write_bytes(child.read_text().encode('utf-16'))
    (root / 'Wiki/Bad-one.md').write_bytes(b'broken\xe2\x82')
    temporal = root / '_Temporal/Logs/Bad-two.md'
    temporal.parent.mkdir(parents=True)
    temporal.write_bytes(b'legacy\x80 text')
    before = snapshot(root)
    with pytest.raises(ValueError) as exc:
        edit.reparent_children(str(root), router, 'Wiki/Old.md', None, to_provided=True)
    assert all(path in str(exc.value) for path in (
        'Wiki/Bad-one.md', '_Temporal/Logs/Bad-two.md', 'Wiki/wiki~old/Child.md'))
    assert snapshot(root) == before
