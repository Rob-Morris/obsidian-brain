"""Automatic lexical maintenance remains green beside a named bad source."""

from dataclasses import replace
from datetime import datetime, timezone
import json

from _application.application import CommandApplication
from _application.maintenance.run import MaintenanceRunRequest
from _application.registry import current_request_resolver
from _bootstrap.maintenance_summary import GroupOutcome
from command_application import application_for, context_for
import check


class Clock:
    def now(self):
        return datetime.now(timezone.utc)


class Sibling:
    def __init__(self, root):
        self.root = root

    def repair(self, family, *, invocation_id):
        request = current_request_resolver().resolve(family.command_id, dict(family.request))
        return application_for(self.root, invocation_id=invocation_id, context_kind='standalone', clock=Clock()).invoke(request)


def test_lexical_pass_skips_bad_source_and_retains_its_finding(command_vault_clone):
    root = command_vault_clone.vault_root
    source = root / 'Projects/Unreadable.md'
    source.write_bytes(b'legacy text\xff')
    index = root / '.brain/local/retrieval-index.json'
    index.unlink()
    context = context_for(root, context_kind='standalone')
    context = replace(context, maintenance=Sibling(root), clock=Clock())
    context = replace(context, access=context.authorisation.bind(context))
    result = CommandApplication(context, context.authorisation.catalogue).invoke(MaintenanceRunRequest())
    assert result.status == 'ok', result
    group = next(group for group in result.result.groups if group.scope == 'lexical')
    assert group.outcome is GroupOutcome.REPAIRED
    assert all(doc['path'] != 'Projects/Unreadable.md' for doc in json.loads(index.read_text())['documents'])
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    assert not inspect_lexical_cache(root).stale
    assert source.read_bytes() == b'legacy text\xff'
    assert any(f['check'] == 'unreadable_file' and f['file'] == 'Projects/Unreadable.md' for f in check.run_checks(root)['findings'])


def test_skipped_lexical_source_becomes_stale_when_repaired_removed_or_replaced(command_vault_clone):
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    from _portable.lexical_maintenance import maintain_lexical_index
    import os
    root = command_vault_clone.vault_root
    source = root / 'Projects/Unreadable.md'
    source.write_bytes(b'legacy\xff')
    maintain_lexical_index(root, dry_run=False, force=True)
    assert not inspect_lexical_cache(root).stale
    source.write_text('---\ntype: living/project\nkey: recovered\n---\nRecovered.', encoding='utf-8')
    data = json.loads((root / '.brain/local/retrieval-index.json').read_text())
    threshold = datetime.fromisoformat(data['meta']['built_at']).timestamp()
    os.utime(source, (threshold + 1, threshold + 1))
    assert inspect_lexical_cache(root).reason == 'document-newer-than-index'
    os.utime(source, None)
    maintain_lexical_index(root, dry_run=False, force=True)
    assert not inspect_lexical_cache(root).stale
    data = json.loads((root / '.brain/local/retrieval-index.json').read_text())
    assert 'Projects/Unreadable.md' not in data['meta']['skipped_paths']
    source.write_bytes(b'legacy\xff')
    maintain_lexical_index(root, dry_run=False, force=True)
    assert not inspect_lexical_cache(root).stale
    source.rename(root / 'Projects/Other Bad.md')
    assert inspect_lexical_cache(root).reason == 'document-path-drift'
    maintain_lexical_index(root, dry_run=False, force=True)
    assert not inspect_lexical_cache(root).stale
    (root / 'Projects/Other Bad.md').unlink()
    assert inspect_lexical_cache(root).reason == 'document-count-drift'


def test_incremental_recovery_removes_skip_inventory(command_vault_clone):
    from _search.index import build_index
    from _portable.lexical_maintenance import update_lexical_documents
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    root = command_vault_clone.vault_root
    source = root / 'Projects/Unreadable.md'
    source.write_bytes(b'legacy\xff')
    index = build_index(root).index
    assert 'Projects/Unreadable.md' in index['meta']['skipped_paths']
    source.write_text('---\ntype: living/project\nkey: recovered\n---\nRecovered.', encoding='utf-8')
    update_lexical_documents(root, index, ('Projects/Unreadable.md',))
    assert 'Projects/Unreadable.md' not in index['meta']['skipped_paths']
    assert not inspect_lexical_cache(root).stale


def test_invalid_skip_inventory_cannot_hide_indexed_documents(command_vault_clone):
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    root = command_vault_clone.vault_root
    path = root / '.brain/local/retrieval-index.json'
    original = json.loads(path.read_text())
    for skipped in ({'path': 'Projects/Missing.md'}, ['same.md', 'same.md'],
                    [original['documents'][0]['path']], [None]):
        data = {**original, 'meta': {**original['meta'], 'skipped_paths': skipped}}
        path.write_text(json.dumps(data))
        assert inspect_lexical_cache(root).reason == 'invalid-skipped-paths'
