"""Private cache layout must work without the Windows long-path registry opt-in."""
import os
from pathlib import Path

import pytest

from _command_interface.result_store import FileResultStore, ResultStoreError, _windows_extended_path


@pytest.mark.parametrize('ordinary,extended', [
    (r'C:\Brain', r'\\?\C:\Brain'),
    (r'\\server\share\Brain', r'\\?\UNC\server\share\Brain'),
    (r'\\?\C:\Brain', r'\\?\C:\Brain'),
    (r'\\?\UNC\server\share\Brain', r'\\?\UNC\server\share\Brain'),
])
def test_extended_storage_root_preserves_drive_unc_and_existing_prefix(ordinary, extended):
    assert _windows_extended_path(ordinary) == extended


@pytest.mark.skipif(os.name != 'nt', reason='native Windows filesystem boundary')
def test_deep_private_cache_resumes_pages_and_clears_without_long_path_opt_in(tmp_path):
    ordinary = tmp_path / ('vault-' + 'a' * 60) / ('nested-' + 'b' * 60)
    vault = Path(_windows_extended_path(str(ordinary)))
    vault.mkdir(parents=True)
    store = FileResultStore(ordinary, 'brain', 'alice')
    credential = store.issue()
    owner = store.authenticate(credential)
    ticket = store.admit(owner, 'test.read', 'invocation')
    result_id = store.publish(owner, ticket, {'result': {'text': '雪'}}, {})
    store.save_view(owner, result_id, 'view', [{'result': {'text': '雪'}}], {})
    assert len(str(store._view(owner, result_id, 'view') / '1.json')) > 300
    assert str(store._root).startswith('\\\\?\\')
    resumed = FileResultStore(ordinary, 'brain', 'alice')
    assert resumed.authenticate(credential) == owner
    assert resumed.read_view_page(owner, result_id, 'view', 1)['result'] == {'text': '雪'}
    resumed.clear(owner)
    with pytest.raises(ResultStoreError):
        resumed.load(owner, result_id)
