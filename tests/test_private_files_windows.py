"""Native Windows security-descriptor acceptance; never simulated as native proof."""
import errno
import os
from pathlib import Path
import subprocess
import sys

import pytest

from _bootstrap import private_files as private


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='requires native Win32 owner/DACL APIs')


def _set_dacl(path, sddl, *, directory):
    """Use real Win32 security APIs to arrange unsafe ACLs, never a policy mock."""
    from ctypes import wintypes as w
    api = private._windows()
    descriptor, dacl = w.LPVOID(), w.LPVOID()
    present, defaulted = w.BOOL(), w.BOOL()
    api._check(api.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, api.c.byref(descriptor), None))
    try:
        api._check(api.security.GetSecurityDescriptorDacl(descriptor, api.c.byref(present),
            api.c.byref(dacl), api.c.byref(defaulted)))
        with api._open(path, directory=directory, access=0x20000 | 0x40000 | 0x80) as handle:
            api._check(api.security.SetSecurityInfo(handle, 1, 0x4 | 0x80000000,
                None, None, dacl, None) == 0)
    finally:
        api.kernel.LocalFree(descriptor)


def _dacl_bytes(path, *, directory):
    api = private._windows()
    with api._open(path, directory=directory) as handle, api._security(handle) as (_, dacl):
        if not dacl:
            return None
        size = api.c.cast(dacl, api.c.POINTER(api.Acl)).contents.size
        return api.c.string_at(dacl, size)


def test_native_create_dir_is_private_and_existing_permissive_dir_is_never_repaired(tmp_path):
    directory = tmp_path / 'private'
    private.create_dir(directory)
    private.validate_existing_dir(directory)
    user = private._windows().user
    for broad in (False, True):
        if broad:
            _set_dacl(directory, f'D:P(A;OICI;FA;;;{user})(A;OICI;GR;;;WD)', directory=True)
        before = _dacl_bytes(directory, directory=True)
        with pytest.raises(private.PrivateFileError) as caught:
            private.create_dir(directory)
        assert caught.value.errno == errno.EEXIST
        assert _dacl_bytes(directory, directory=True) == before
    with pytest.raises(private.PrivateFileError):
        private.validate_existing_dir(directory)


def test_native_private_creation_validates_before_write_and_resumes_in_new_process(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    private.validate_existing_dir(directory)
    # Windows mode bits look permissive even when the real DACL is private.
    assert directory.stat().st_mode & 0o077
    destination = directory / 'session'
    original = private._write_all

    def before_write(descriptor, data):
        private.validate_fd(descriptor)
        assert not os.get_inheritable(descriptor)
        assert os.fstat(descriptor).st_size == 0
        original(descriptor, data)

    monkeypatch.setattr(private, '_write_all', before_write)
    private.create_secret(destination, b'native secret')
    code = ('import os,sys; from _bootstrap.private_files import validate_fd; '
            'fd=os.open(sys.argv[1],os.O_RDONLY|os.O_BINARY); validate_fd(fd); '
            'assert os.read(fd,512)==b"native secret"; os.close(fd)')
    scripts = str(Path(__file__).resolve().parents[1] / 'src/brain-core/scripts')
    completed = subprocess.run([sys.executable, '-B', '-c', code, str(destination)],
        env={**os.environ, 'PYTHONPATH': scripts, 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert 'native secret' not in completed.stdout
    with pytest.raises(private.PrivateFileError):
        private.create_secret(destination, b'replacement')
    assert destination.read_bytes() == b'native secret'
    assert list(directory.iterdir()) == [destination]


def test_native_permissive_existing_file_is_refused_without_acl_repair(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    destination = directory / 'session'
    private.create_secret(destination, b'secret')
    user = private._windows().user
    _set_dacl(destination, f'D:P(A;;FA;;;{user})(A;;GR;;;WD)', directory=False)
    before = _dacl_bytes(destination, directory=False)
    descriptor = os.open(destination, os.O_RDONLY | os.O_BINARY)
    try:
        with pytest.raises(private.PrivateFileError):
            private.validate_fd(descriptor)
    finally:
        os.close(descriptor)
    assert _dacl_bytes(destination, directory=False) == before
    assert destination.read_bytes() == b'secret'


def test_native_managed_directory_can_be_secured_but_validation_does_not_repair(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    user = private._windows().user
    _set_dacl(directory, f'D:P(A;OICI;FA;;;{user})(A;OICI;GR;;;WD)', directory=True)
    before = _dacl_bytes(directory, directory=True)
    with pytest.raises(private.PrivateFileError):
        private.validate_existing_dir(directory)
    assert _dacl_bytes(directory, directory=True) == before
    assert list(directory.iterdir()) == []
    private.protect_dir(directory)
    private.validate_existing_dir(directory)
    private.create_secret(directory / 'session', b'safe')


def test_native_repeated_protection_of_private_directory_never_sets_or_propagates_acl(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    private.create_dir(directory)
    private.create_dir(directory / 'child')
    leaf = directory / 'child' / 'session'
    private.create_secret(leaf, b'unchanged child')
    before = _dacl_bytes(leaf, directory=False)

    def forbidden(*args):
        pytest.fail('healthy managed directory attempted recursive DACL propagation')

    # Keep real native inspection; trap only the prohibited ACL-write boundary.
    monkeypatch.setattr(private._windows().security, 'SetSecurityInfo', forbidden)
    for _ in range(3):
        private.protect_dir(directory)
        private.validate_existing_dir(directory)
    assert _dacl_bytes(leaf, directory=False) == before
    assert leaf.read_bytes() == b'unchanged child'


def test_native_readable_parent_creates_private_secret_before_writing_without_acl_repair(tmp_path, monkeypatch):
    directory = tmp_path / 'readable'
    private.create_dir(directory)
    user = private._windows().user
    _set_dacl(directory, f'D:P(A;OICI;FA;;;{user})(A;OICI;GRGX;;;WD)', directory=True)
    before = _dacl_bytes(directory, directory=True)
    with pytest.raises(private.PrivateFileError):
        private.validate_existing_dir(directory)
    original = private._write_all
    calls = []

    def before_write(descriptor, data):
        private.validate_fd(descriptor)
        assert os.fstat(descriptor).st_size == 0
        calls.append(data)
        original(descriptor, data)

    monkeypatch.setattr(private, '_write_all', before_write)
    destination = directory / 'session'
    private.create_secret(destination, b'private in readable parent')
    assert calls == [b'private in readable parent']
    descriptor = os.open(destination, os.O_RDONLY | os.O_BINARY)
    try:
        private.validate_fd(descriptor)
        assert os.read(descriptor, 512) == b'private in readable parent'
    finally:
        os.close(descriptor)
    assert _dacl_bytes(directory, directory=True) == before


def test_native_null_dacl_is_not_private(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    _set_dacl(directory, 'D:NO_ACCESS_CONTROL', directory=True)
    assert _dacl_bytes(directory, directory=True) is None
    with pytest.raises(private.PrivateFileError):
        private.validate_existing_dir(directory)


def test_native_hardlinked_credentials_retain_the_same_privacy_proof(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    destination = directory / 'session'
    private.create_secret(destination, b'secret')
    os.link(destination, directory / 'alias')
    descriptor = os.open(destination, os.O_RDONLY | os.O_BINARY)
    try:
        private.validate_fd(descriptor)
        assert os.read(descriptor, 512) == b'secret'
    finally:
        os.close(descriptor)


def test_native_reparse_directories_and_ancestors_are_refused(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    link = tmp_path / 'reparse'
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows host does not permit symlink creation')
        raise
    for action in (private.validate_existing_dir, private.protect_dir):
        with pytest.raises(private.PrivateFileError):
            action(link)
    with pytest.raises(private.PrivateFileError):
        private.create_secret(link / 'session', b'never written')
    assert list(directory.iterdir()) == []


def test_native_open_private_file_returns_read_only_validated_fd_and_preserves_enoent(tmp_path):
    destination = tmp_path / 'session'
    private.create_secret(destination, b'native read credential')
    descriptor = private.open_private_file(destination)
    try:
        assert not os.get_inheritable(descriptor)
        assert os.read(descriptor, 513) == b'native read credential'
        with pytest.raises(OSError):
            os.write(descriptor, b'not writable')
    finally:
        os.close(descriptor)
    with pytest.raises(private.PrivateFileError) as missing:
        private.open_private_file(tmp_path / 'absent')
    assert missing.value.errno == errno.ENOENT
    user = private._windows().user
    _set_dacl(destination, f'D:P(A;;FA;;;{user})(A;;GR;;;WD)', directory=False)
    before = _dacl_bytes(destination, directory=False)
    with pytest.raises(private.PrivateFileError) as unsafe:
        private.open_private_file(destination)
    assert unsafe.value.errno != errno.ENOENT
    assert _dacl_bytes(destination, directory=False) == before


def test_native_open_private_file_rejects_original_reparse_leaf(tmp_path):
    destination = tmp_path / 'session'
    private.create_secret(destination, b'secret')
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(destination)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows host does not permit symlink creation')
        raise
    with pytest.raises(private.PrivateFileError) as linked:
        private.open_private_file(alias)
    assert linked.value.errno != errno.ENOENT
