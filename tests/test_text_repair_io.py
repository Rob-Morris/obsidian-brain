"""Real filesystem security regressions for bounded text repair."""

import hashlib
import os
from contextlib import contextmanager

import pytest

from _lifecycle import text_repair_io as io


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / 'vault'
    parent = root / 'Notes'
    parent.mkdir(parents=True)
    target = parent / 'note.md'
    target.write_bytes(b'original\r\n')
    return root, target


def test_read_and_repair_preserve_bytes(candidate):
    root, target = candidate
    original = io.read_candidate(root, 'Notes/note.md')
    assert original == b'original\r\n'
    assert io.repair_candidate(root, 'Notes/note.md', digest(original), b'fixed\r\n')
    assert target.read_bytes() == b'fixed\r\n'
    assert list(target.parent.iterdir()) == [target]


def test_missing_and_digest_change_skip(candidate):
    root, target = candidate
    assert not io.repair_candidate(root, 'Notes/note.md', digest(b'wrong'), b'fixed')
    assert target.read_bytes() == b'original\r\n'
    target.unlink()
    assert not io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    with pytest.raises(FileNotFoundError):
        io.read_candidate(root, 'Notes/note.md')


@pytest.mark.parametrize('relative', ['../outside.md', '/absolute.md', 'Notes/../note.md', 'Notes\\note.md'])
def test_invalid_relative_refused(candidate, relative):
    root, _ = candidate
    with pytest.raises(io.TextRepairIOError):
        io.read_candidate(root, relative)
    with pytest.raises(io.TextRepairIOError):
        io.repair_candidate(root, relative, digest(b''), b'fixed')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX symlink and descriptor contract')
@pytest.mark.parametrize('link_at', ['root', 'ancestor', 'parent', 'final'])
def test_symlink_refusal(candidate, tmp_path, link_at):
    root, target = candidate
    if link_at == 'final':
        protected = root / '.brain-core' / 'protected.md'
        protected.parent.mkdir()
        protected.write_bytes(b'protected')
        target.unlink()
        target.symlink_to(protected)
    elif link_at == 'parent':
        original = root / 'real-notes'
        target.parent.rename(original)
        (root / 'Notes').symlink_to(original, target_is_directory=True)
    elif link_at == 'root':
        original = tmp_path / 'real-vault'
        root.rename(original)
        root.symlink_to(original, target_is_directory=True)
    else:
        alias = tmp_path / 'alias'
        alias.symlink_to(tmp_path, target_is_directory=True)
        root = alias / 'vault'
    with pytest.raises(io.TextRepairIOError):
        io.read_candidate(root, 'Notes/note.md')
    with pytest.raises(io.TextRepairIOError):
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'')
    if link_at == 'final':
        assert protected.read_bytes() == b'protected'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX deterministic pathname race')
@pytest.mark.parametrize('boundary', ['digest', 'stage', 'replace'])
@pytest.mark.parametrize('destination', ['outside', 'protected'])
def test_parent_swap_never_writes_redirected_subject(candidate, tmp_path, monkeypatch,
                                                     boundary, destination):
    root, target = candidate
    other = tmp_path / 'outside' if destination == 'outside' else root / '.brain-core'
    other.mkdir()
    protected = other / target.name
    protected.write_bytes(b'protected')
    detached = root / 'detached'

    def swap():
        target.parent.rename(detached)
        target.parent.symlink_to(other, target_is_directory=True)

    if boundary == 'digest':
        real = io._matches
        def matches(data, expected):
            result = real(data, expected)
            swap()
            return result
        monkeypatch.setattr(io, '_matches', matches)
    elif boundary == 'stage':
        real = os.fsync
        def fsync(fd):
            real(fd)
            swap()
        monkeypatch.setattr(os, 'fsync', fsync)
    else:
        real = os.replace
        def replace(src, dst, **kwargs):
            swap()
            assert kwargs['src_dir_fd'] == kwargs['dst_dir_fd']
            real(src, dst, **kwargs)
        monkeypatch.setattr(os, 'replace', replace)
    if boundary == 'replace':
        with pytest.raises(io.TextRepairIOError, match='committed to original directory') as error:
            io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
        assert error.value.path == 'Notes/note.md'
        assert error.value.committed is True
        assert (detached / 'note.md').read_bytes() == b'fixed'
    else:
        assert not io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
        assert (detached / 'note.md').read_bytes() == b'original\r\n'
    assert protected.read_bytes() == b'protected'
    assert sorted(p.name for p in detached.iterdir()) == ['note.md']


@pytest.mark.parametrize('failure', ['write', 'fsync', 'replace'])
def test_write_failure_cleans_temporary(candidate, monkeypatch, failure):
    root, target = candidate
    def fail(*args, **kwargs):
        raise OSError('injected disk failure')
    if failure == 'write':
        real = os.fdopen
        @contextmanager
        def fdopen(fd, mode, **kwargs):
            with real(fd, mode, **kwargs) as stream:
                if mode == 'wb':
                    class FailingWriter:
                        write = staticmethod(fail)
                    yield FailingWriter()
                else:
                    yield stream
        monkeypatch.setattr(os, 'fdopen', fdopen)
    elif failure == 'replace' and os.name == 'nt':
        monkeypatch.setattr(io._WindowsHandles, 'rename', fail)
    else:
        monkeypatch.setattr(os, failure, fail)
    with pytest.raises(io.TextRepairIOError, match='Notes/note.md') as error:
        io.repair_candidate(root, 'Notes/note.md', digest(target.read_bytes()), b'fixed')
    assert error.value.committed is False
    assert target.read_bytes() == b'original\r\n'
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'posix', reason='POSIX final-file staging race')
def test_final_symlink_at_staging_never_truncates(candidate, tmp_path, monkeypatch):
    root, target = candidate
    protected = tmp_path / 'protected.md'
    protected.write_bytes(b'protected')
    real = os.fsync
    def fsync(fd):
        real(fd)
        target.unlink()
        target.symlink_to(protected)
    monkeypatch.setattr(os, 'fsync', fsync)
    with pytest.raises(io.TextRepairIOError):
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'')
    assert protected.read_bytes() == b'protected'
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows directory handles')
def test_windows_parent_locks_and_native_rename(candidate, monkeypatch):
    root, target = candidate
    real = io._WindowsHandles.rename
    def rename(native, stage, parent, name):
        for directory in [target.parent, root, root.parent]:
            with pytest.raises(OSError):
                directory.rename(directory.with_name(directory.name + '-moved'))
        assert name == target.name
        assert len(list(target.parent.glob('note.md.*.tmp'))) == 1
        return real(native, stage, parent, name)
    monkeypatch.setattr(io._WindowsHandles, 'rename', rename)
    assert io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed\r\n')
    assert target.read_bytes() == b'fixed\r\n'
    assert list(target.parent.iterdir()) == [target]
    target.parent.rename(root / 'moved')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX non-regular file flags')
def test_non_regular_refused_without_blocking(candidate):
    root, target = candidate
    target.unlink()
    os.mkfifo(target)
    with pytest.raises(io.TextRepairIOError):
        io.read_candidate(root, 'Notes/note.md')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX descriptor ownership')
def test_exception_closes_every_descriptor(candidate, monkeypatch):
    root, target = candidate
    opened = set()
    real_open, real_close = os.open, os.close
    def tracked_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened.add(fd)
        return fd
    def tracked_close(fd):
        real_close(fd)
        opened.remove(fd)
    def fail(fd):
        raise OSError('disk failure')
    monkeypatch.setattr(os, 'open', tracked_open)
    monkeypatch.setattr(os, 'close', tracked_close)
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(io.TextRepairIOError):
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert not opened
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'posix', reason='POSIX anchored root identity')
def test_root_swapped_during_staging_skips(candidate, tmp_path, monkeypatch):
    root, target = candidate
    outside = tmp_path / 'outside'
    (outside / 'Notes').mkdir(parents=True)
    protected = outside / 'Notes' / 'note.md'
    protected.write_bytes(b'protected')
    detached = tmp_path / 'detached'
    real = os.fsync
    def fsync(fd):
        real(fd)
        root.rename(detached)
        root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(os, 'fsync', fsync)
    assert not io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert protected.read_bytes() == b'protected'
    assert (detached / 'Notes' / 'note.md').read_bytes() == b'original\r\n'
    assert len(list((detached / 'Notes').iterdir())) == 1


@pytest.mark.skipif(os.name != 'posix', reason='POSIX concurrent final-file edit')
def test_final_changed_during_staging_skips(candidate, monkeypatch):
    root, target = candidate
    real = os.fsync
    def fsync(fd):
        real(fd)
        target.write_bytes(b'changed')
    monkeypatch.setattr(os, 'fsync', fsync)
    assert not io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert target.read_bytes() == b'changed'
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows reparse-point validation')
@pytest.mark.parametrize('link_at', ['root', 'parent', 'final'])
def test_windows_reparse_points_refused(candidate, tmp_path, link_at):
    root, target = candidate
    outside = tmp_path / 'outside'
    outside.mkdir()
    protected = outside / 'note.md'
    protected.write_bytes(b'protected')
    if link_at == 'root':
        root = tmp_path / 'linked-vault'
        link, destination, directory = root, outside, True
    elif link_at == 'parent':
        target.unlink()
        target.parent.rmdir()
        link, destination, directory = target.parent, outside, True
    else:
        target.unlink()
        link, destination, directory = target, protected, False
    try:
        link.symlink_to(destination, target_is_directory=directory)
    except OSError as exc:
        if exc.winerror == 1314:
            pytest.skip('Windows symlink privilege unavailable')
        raise
    with pytest.raises(io.TextRepairIOError):
        io.read_candidate(root, 'Notes/note.md')
    with pytest.raises(io.TextRepairIOError):
        io.repair_candidate(root, 'Notes/note.md', digest(b'protected'), b'')
    assert protected.read_bytes() == b'protected'


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows post-replace handle cleanup')
@pytest.mark.parametrize('failure', [OSError, FileNotFoundError])
def test_windows_post_replace_failure_is_committed(candidate, monkeypatch, failure):
    root, target = candidate
    real_rename = io._WindowsHandles.rename
    real_close = io._WindowsHandles.close
    written = False
    raised = False
    def rename(native, *args):
        nonlocal written
        result = real_rename(native, *args)
        written = True
        return result
    def close(native, handle):
        nonlocal raised
        real_close(native, handle)
        if written and not raised:
            raised = True
            raise failure('post-replace cleanup failure')
    monkeypatch.setattr(io._WindowsHandles, 'rename', rename)
    monkeypatch.setattr(io._WindowsHandles, 'close', close)
    with pytest.raises(io.TextRepairIOError) as error:
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert error.value.committed is True
    assert error.value.path == 'Notes/note.md'
    assert target.read_bytes() == b'fixed'
    target.parent.rename(root / 'moved')


@pytest.mark.parametrize('missing', ['file', 'parent', 'root'])
def test_read_disappearance_preserves_file_not_found(candidate, missing):
    root, target = candidate
    target.unlink()
    if missing in ('parent', 'root'):
        target.parent.rmdir()
    if missing == 'root':
        root.rmdir()
    with pytest.raises(FileNotFoundError):
        io.read_candidate(root, 'Notes/note.md')
    assert not io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')


def test_named_error_defaults_to_uncommitted():
    error = io.TextRepairIOError('Notes/note.md', OSError('failure'))
    assert error.path == 'Notes/note.md'
    assert error.committed is False


@pytest.mark.skipif(os.name != 'posix', reason='POSIX post-replace validation and cleanup')
@pytest.mark.parametrize('boundary', ['validation', 'close'])
@pytest.mark.parametrize('failure', [OSError, FileNotFoundError])
def test_posix_post_replace_failure_keeps_committed(candidate, monkeypatch, boundary, failure):
    root, target = candidate
    real_replace, real_close, real_current = os.replace, os.close, io._parent_current
    written = False
    raised = False
    def replace(*args, **kwargs):
        nonlocal written
        real_replace(*args, **kwargs)
        written = True
    def close(fd):
        nonlocal raised
        real_close(fd)
        if boundary == 'close' and written and not raised:
            raised = True
            raise failure('post-replace close failure')
    def current(edges):
        if boundary == 'validation' and written:
            raise failure('post-replace validation failure')
        return real_current(edges)
    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(os, 'close', close)
    monkeypatch.setattr(io, '_parent_current', current)
    with pytest.raises(io.TextRepairIOError) as error:
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert error.value.path == 'Notes/note.md'
    assert error.value.committed is True
    assert target.read_bytes() == b'fixed'
    assert list(target.parent.iterdir()) == [target]


class WindowsReparseAttacker:
    """Real FILE_WRITE_ATTRIBUTES opens and FSCTL junction buffers."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.native = io._WindowsHandles()
        self.api = self.native.api
        self.api.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPVOID, wintypes.DWORD,
                                            wintypes.LPVOID, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        self.api.DeviceIoControl.restype = wintypes.BOOL

    def attributes_handle(self, directory):
        handle = self.api.CreateFileW(str(directory), 0x100, 7, None, 3,
                                      0x02000000 | 0x00200000, None)
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return handle

    def redirect(self, handle, destination):
        import struct
        from ctypes import wintypes
        # Microsoft's REPARSE_DATA_BUFFER.MountPointReparseBuffer, byte offsets.
        substitute = ('\\??\\' + str(destination)).encode('utf-16-le')
        printable = str(destination).encode('utf-16-le')
        paths = substitute + b'\0\0' + printable + b'\0\0'
        data = struct.pack('<HHHH', 0, len(substitute), len(substitute) + 2,
                           len(printable)) + paths
        payload = struct.pack('<IHH', 0xA0000003, len(data), 0) + data
        buffer = self.ctypes.create_string_buffer(payload)
        returned = wintypes.DWORD()
        if not self.api.DeviceIoControl(handle, 0x000900A4, buffer, len(payload),
                                        None, 0, self.ctypes.byref(returned), None):
            raise self.ctypes.WinError(self.ctypes.get_last_error())


@pytest.mark.skipif(os.name != 'nt', reason='Native FSCTL_SET_REPARSE_POINT races')
@pytest.mark.parametrize('opened', ['before', 'during'])
@pytest.mark.parametrize('boundary', ['read', 'create', 'rename'])
@pytest.mark.parametrize('destination', ['outside', 'protected'])
def test_windows_attribute_handle_cannot_redirect_repair(candidate, tmp_path, monkeypatch,
                                                         opened, boundary, destination):
    from contextlib import ExitStack
    root, target = candidate
    other = tmp_path / 'outside' if destination == 'outside' else root / '.brain-core'
    other.mkdir()
    protected = other / target.name
    protected.write_bytes(b'protected')
    attacker = WindowsReparseAttacker()
    # Prove the attack buffer and attribute-only access really can set a junction.
    control = tmp_path / 'empty-control'
    control.mkdir()
    handle = attacker.attributes_handle(control)
    try:
        attacker.redirect(handle, other)
    finally:
        attacker.native.close(handle)
    control.rmdir()
    attempts = []
    with ExitStack() as stack:
        handle = None
        if opened == 'before':
            handle = attacker.attributes_handle(target.parent)
            stack.callback(attacker.native.close, handle)

        def attack():
            nonlocal handle
            if handle is None:
                handle = attacker.attributes_handle(target.parent)
                stack.callback(attacker.native.close, handle)
            # Try to empty the directory, including its staging entry. At least
            # one entry must be locked throughout the read/create/rename handoff.
            refused = []
            for entry in target.parent.iterdir():
                try:
                    entry.unlink()
                except OSError:
                    refused.append(entry.name)
            assert refused
            if boundary == 'rename':
                assert any(name.endswith('.tmp') for name in refused)
            else:
                assert target.name in refused
            with pytest.raises(OSError) as error:
                attacker.redirect(handle, other)
            assert error.value.winerror == 145  # ERROR_DIR_NOT_EMPTY
            with pytest.raises(OSError):
                target.parent.rmdir()
            attempts.append(refused)

        real_read, real_open, real_rename = io._windows_read_fd, io._WindowsHandles.open, io._WindowsHandles.rename
        if boundary == 'read':
            def read(fd):
                data = real_read(fd)
                if not attempts:
                    attack()
                return data
            monkeypatch.setattr(io, '_windows_read_fd', read)
        elif boundary == 'create':
            def create(native, parent, name, **kwargs):
                if kwargs.get('create'):
                    attack()
                return real_open(native, parent, name, **kwargs)
            monkeypatch.setattr(io._WindowsHandles, 'open', create)
        else:
            def rename(native, stage, parent, name):
                attack()
                return real_rename(native, stage, parent, name)
            monkeypatch.setattr(io._WindowsHandles, 'rename', rename)
        assert io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed\r\n') is True
        assert len(attempts) == 1
        assert target.read_bytes() == b'fixed\r\n'
        assert list(target.parent.iterdir()) == [target]
        assert protected.read_bytes() == b'protected'
        assert list(other.iterdir()) == [protected]
    target.parent.rename(root / 'moved')


@pytest.mark.skipif(os.name != 'nt', reason='Native reparse change before final open')
@pytest.mark.parametrize('operation', ['read', 'repair'])
def test_windows_parent_reparse_before_final_open_refuses(candidate, tmp_path, monkeypatch, operation):
    root, target = candidate
    outside = tmp_path / 'outside'
    outside.mkdir()
    protected = outside / target.name
    protected.write_bytes(b'original\r\n')
    attacker = WindowsReparseAttacker()
    handle = attacker.attributes_handle(target.parent)
    real = io._WindowsHandles.open
    attacked = False
    def open_file(native, parent, name, **kwargs):
        nonlocal attacked
        if name == target.name and not kwargs.get('directory') and not attacked:
            attacked = True
            target.unlink()
            attacker.redirect(handle, outside)
        return real(native, parent, name, **kwargs)
    monkeypatch.setattr(io._WindowsHandles, 'open', open_file)
    try:
        if operation == 'read':
            with pytest.raises((io.TextRepairIOError, FileNotFoundError)):
                io.read_candidate(root, 'Notes/note.md')
        else:
            try:
                result = io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
            except io.TextRepairIOError as error:
                assert error.committed is False
            else:
                assert result is False
        assert attacked
        assert protected.read_bytes() == b'original\r\n'
        assert list(outside.iterdir()) == [protected]
    finally:
        attacker.native.close(handle)
        target.parent.rmdir()


@pytest.mark.skipif(os.name != 'nt', reason='Native staging handle transfer cleanup')
def test_windows_stage_fd_transfer_failure_deletes_exact_stage(candidate, monkeypatch):
    import msvcrt
    root, target = candidate
    real = msvcrt.open_osfhandle
    def transfer(handle, flags):
        if flags & os.O_WRONLY:
            raise OSError('injected stage ownership transfer failure')
        return real(handle, flags)
    monkeypatch.setattr(msvcrt, 'open_osfhandle', transfer)
    with pytest.raises(io.TextRepairIOError) as error:
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert error.value.committed is False
    assert target.read_bytes() == b'original\r\n'
    assert list(target.parent.iterdir()) == [target]
    target.parent.rename(root / 'moved')


@pytest.mark.skipif(os.name != 'nt', reason='Native NTSTATUS mapping and uncommitted rename failure')
def test_windows_native_rename_failure_maps_status_and_cleans_stage(candidate, monkeypatch):
    import ctypes
    root, target = candidate
    def rename(native, handle, parent, name):
        native.check_status(ctypes.c_int32(0xC0000022).value)  # STATUS_ACCESS_DENIED
    monkeypatch.setattr(io._WindowsHandles, 'rename', rename)
    with pytest.raises(io.TextRepairIOError) as error:
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert error.value.path == 'Notes/note.md'
    assert error.value.committed is False
    assert error.value.__cause__.winerror == 5
    assert target.read_bytes() == b'original\r\n'
    assert list(target.parent.iterdir()) == [target]
    target.parent.rename(root / 'moved')


@pytest.mark.skipif(os.name != 'nt', reason='Native staged CRT descriptor close after commit')
def test_windows_stage_close_failure_is_committed(candidate, monkeypatch):
    root, target = candidate
    real_rename, real_close = io._WindowsHandles.rename, os.close
    written = False
    raised = False
    def rename(native, *args):
        nonlocal written
        result = real_rename(native, *args)
        written = True
        return result
    def close(fd):
        nonlocal raised
        real_close(fd)
        if written and not raised:
            raised = True
            raise OSError('post-commit staged descriptor close failure')
    monkeypatch.setattr(io._WindowsHandles, 'rename', rename)
    monkeypatch.setattr(os, 'close', close)
    with pytest.raises(io.TextRepairIOError) as error:
        io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed')
    assert error.value.committed is True
    assert error.value.path == 'Notes/note.md'
    assert target.read_bytes() == b'fixed'
    assert list(target.parent.iterdir()) == [target]
    target.parent.rename(root / 'moved')


@pytest.mark.parametrize('root_text', [
    r'C:\vault',
    r'\\server\share\vault',
    r'\\?\C:\vault',
    r'\\?\UNC\server\share\vault',
    r'\\?\Volume{11111111-1111-1111-1111-111111111111}\vault',
])
def test_windows_anchor_shapes_use_same_relative_walk(root_text, monkeypatch):
    import ctypes
    from pathlib import PureWindowsPath
    root = PureWindowsPath(root_text)
    native = object.__new__(io._WindowsHandles)
    native.ctypes = ctypes
    calls = []
    closed = []
    class Api:
        def CreateFileW(self, *args):
            calls.append(args)
            return 123
    native.api = Api()
    native.validate = lambda handle, **kwargs: None
    assert native.anchor(root) == 123
    assert calls == [(root.anchor, native.DIRECTORY_ACCESS, 3, None, 3,
                      0x02000000 | 0x00200000, None)]
    children = []
    def open_child(parent, name, **kwargs):
        assert kwargs == {'directory': True}
        children.append((parent, name))
        return parent + 1
    native.open = open_child
    native.close = closed.append
    monkeypatch.setattr(io, '_WindowsHandles', lambda: native)
    with io._windows_parent(root, ['Notes', 'note.md']) as (_, parent, directories):
        assert parent == 125
        assert directories == [123, 124, 125]
    assert children == [(123, 'vault'), (124, 'Notes')]
    assert closed == [125, 124, 123]


@pytest.mark.skipif(os.name != 'nt', reason='Native extended drive filesystem I/O')
def test_windows_extended_drive_root(candidate):
    from pathlib import Path
    root, target = candidate
    if str(root).startswith('\\\\'):
        pytest.skip('Fixture is on a UNC share; covered by UNC integration test')
    extended = Path('\\\\?\\' + str(root))
    assert io.read_candidate(extended, 'Notes/note.md') == b'original\r\n'
    assert io.repair_candidate(extended, 'Notes/note.md', digest(b'original\r\n'), b'fixed\r\n')
    assert target.read_bytes() == b'fixed\r\n'
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.skipif(os.name != 'nt', reason='Native UNC share filesystem I/O')
@pytest.mark.parametrize('extended', [False, True])
def test_windows_unc_share_read_and_repair(extended):
    from pathlib import Path, PureWindowsPath
    from tempfile import TemporaryDirectory
    share_root = os.environ.get('BRAIN_TEST_UNC_ROOT')
    if not share_root:
        pytest.skip('Set BRAIN_TEST_UNC_ROOT to an existing writable UNC test directory')
    assert PureWindowsPath(share_root).anchor.startswith('\\\\')
    with TemporaryDirectory(prefix='brain-repair-io-', dir=share_root) as directory:
        root = Path(directory)
        parent = root / 'Notes'
        parent.mkdir()
        target = parent / 'note.md'
        target.write_bytes(b'original\r\n')
        if extended and not str(root).startswith('\\\\?\\'):
            root = Path('\\\\?\\UNC\\' + str(root)[2:])
        assert io.read_candidate(root, 'Notes/note.md') == b'original\r\n'
        assert io.repair_candidate(root, 'Notes/note.md', digest(b'original\r\n'), b'fixed\r\n')
        assert target.read_bytes() == b'fixed\r\n'
        assert list(parent.iterdir()) == [target]


@pytest.mark.parametrize('name', ['note.md', 'café-🧠.md'])
def test_windows_open_constructs_actual_unicode_boundary(name, monkeypatch):
    import ctypes
    import gc
    from ctypes import wintypes
    from types import SimpleNamespace

    calls = []
    native = None
    def create(handle_out, access, attributes, status, allocation, file_attrs,
               sharing, disposition, options, ea, ea_length):
        # Inspect the actual helper-owned structures at its native call boundary.
        # Only the DLL call is replaced; construction and pointer conversion run.
        gc.collect()
        attrs = ctypes.cast(attributes, ctypes.POINTER(native.object_attributes)).contents
        text = attrs.ObjectName.contents
        assert text.Buffer == name
        assert text.Length == len(name.encode('utf-16-le'))
        assert text.MaximumLength == text.Length + 2
        assert attrs.RootDirectory == 123
        assert attrs.Attributes == 0x40 | 0x1000
        assert sharing == 3
        assert disposition == 1
        assert options & 0x00200000
        ctypes.cast(handle_out, ctypes.POINTER(wintypes.HANDLE)).contents.value = 456
        calls.append(name)
        return 0

    def unused(*args):
        raise AssertionError('unexpected native call')

    kernel = SimpleNamespace(CreateFileW=unused, CloseHandle=unused,
                             GetFileInformationByHandleEx=unused, GetFileType=unused)
    nt = SimpleNamespace(NtCreateFile=create, NtSetInformationFile=unused,
                         RtlNtStatusToDosError=unused)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: kernel if name == 'kernel32' else nt,
                        raising=False)
    native = io._WindowsHandles()
    validated = []
    native.validate = lambda handle, **kwargs: validated.append((handle, kwargs))
    assert native.open(123, name) == 456
    assert calls == [name]
    assert validated == [(456, {'directory': False})]
