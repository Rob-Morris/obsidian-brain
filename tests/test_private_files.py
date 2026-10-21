"""Private-file policy and POSIX behaviour; native Windows has separate tests."""
import errno
import os
import stat

import pytest

from _bootstrap import private_files as private


USER = 'S-1-5-21-100-200-300-1001'
OTHER = 'S-1-5-21-100-200-300-1002'


@pytest.mark.parametrize('owner,aces,expected', [
    (USER, [(0, USER)], True),
    (USER, [(0, USER), (0, 'S-1-5-18'), (0, 'S-1-5-32-544')], True),
    ('S-1-5-18', [(0, USER)], True),
    ('S-1-5-32-544', [(0, USER)], True),
    (USER, [], True),
    (USER, None, False),
    (OTHER, [(0, USER)], False),
    (USER, [(0, OTHER)], False),
    (USER, [(0, 'S-1-1-0')], False),
    (USER, [(1, OTHER), (0, USER)], True),
    (USER, [(1, OTHER), (0, OTHER)], False),
    (USER, [(5, USER)], False),
    (USER, [(9, USER)], False),
])
def test_windows_acl_policy_is_conservative(owner, aces, expected):
    assert private._acl_is_private(owner, USER, aces) is expected


posix = pytest.mark.skipif(os.name == 'nt', reason='POSIX ownership/mode implementation')


@posix
def test_create_dir_is_private_and_refuses_existing_without_repair(tmp_path):
    directory = tmp_path / 'private'
    private.create_dir(directory)
    private.validate_existing_dir(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for mode in (0o700, 0o755):
        directory.chmod(mode)
        with pytest.raises(private.PrivateFileError) as caught:
            private.create_dir(directory)
        assert caught.value.errno == errno.EEXIST
        assert stat.S_IMODE(directory.stat().st_mode) == mode
    linked = tmp_path / 'linked'
    linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(private.PrivateFileError) as caught:
        private.create_dir(linked)
    assert caught.value.errno == errno.EEXIST
    assert linked.is_symlink()


@posix
def test_posix_protect_is_managed_but_validation_never_repairs(tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(private.PrivateFileError):
        private.validate_existing_dir(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    private.protect_dir(directory)
    private.validate_existing_dir(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    child = directory / 'child'
    private.protect_dir(child)
    private.validate_existing_dir(child)


@posix
def test_posix_credential_creation_round_trip_no_overwrite_and_no_stages(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    destination = directory / 'session'
    private.create_secret(destination, b'first credential')
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        private.validate_fd(descriptor)
        assert os.read(descriptor, 512) == b'first credential'
    finally:
        os.close(descriptor)
    with pytest.raises(private.PrivateFileError) as caught:
        private.create_secret(destination, b'replacement credential')
    assert caught.value.errno == errno.EEXIST
    assert destination.read_bytes() == b'first credential'
    assert sorted(path.name for path in directory.iterdir()) == ['session']


@posix
def test_posix_validators_reject_symlinks_and_permissive_credentials_but_allow_hardlinks(tmp_path):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    destination = directory / 'session'
    private.create_secret(destination, b'secret')
    destination.chmod(0o644)
    descriptor = os.open(destination, os.O_RDONLY)
    try:
        with pytest.raises(private.PrivateFileError):
            private.validate_fd(descriptor)
    finally:
        os.close(descriptor)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    destination.chmod(0o600)
    os.link(destination, directory / 'alias')
    descriptor = os.open(destination, os.O_RDONLY)
    try:
        private.validate_fd(descriptor)
        assert os.read(descriptor, 512) == b'secret'
    finally:
        os.close(descriptor)
    link = tmp_path / 'symlink'
    link.symlink_to(directory, target_is_directory=True)
    for action in (private.protect_dir, private.validate_existing_dir):
        with pytest.raises(private.PrivateFileError):
            action(link)
    with pytest.raises(private.PrivateFileError):
        private.create_secret(link / 'new-secret', b'secret')
    assert not (directory / 'new-secret').exists()


@posix
def test_posix_refuses_foreign_owner_without_tightening(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    monkeypatch.setattr(private.os, 'getuid', lambda: directory.stat().st_uid + 1)
    with pytest.raises(private.PrivateFileError):
        private.protect_dir(directory)
    with pytest.raises(private.PrivateFileError):
        private.validate_existing_dir(directory)


@posix
def test_privacy_is_verified_before_any_secret_write(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    private.protect_dir(directory)
    original = private._write_all
    calls = []

    def observe(descriptor, data):
        private.validate_fd(descriptor)
        assert os.fstat(descriptor).st_size == 0
        calls.append(data)
        original(descriptor, data)

    monkeypatch.setattr(private, '_write_all', observe)
    private.create_secret(directory / 'session', b'secret')
    assert calls == [b'secret']
    directory.chmod(0o755)
    private.create_secret(directory / 'second', b'second secret')
    assert calls == [b'secret', b'second secret']
    assert stat.S_IMODE(directory.stat().st_mode) == 0o755
    assert stat.S_IMODE((directory / 'second').stat().st_mode) == 0o600


@posix
def test_failed_write_is_sanitised_and_removes_unpublished_stage(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    private.protect_dir(directory)

    def broken(descriptor, data):
        os.write(descriptor, data[:3])
        raise OSError('private-secret-and-backend-path')

    monkeypatch.setattr(private, '_write_all', broken)
    with pytest.raises(private.PrivateFileError) as caught:
        private.create_secret(directory / 'session', b'private-secret')
    assert 'private-secret' not in str(caught.value)
    assert str(directory) not in str(caught.value)
    assert list(directory.iterdir()) == []


def test_invalid_content_is_refused_before_filesystem_work(tmp_path):
    before = list(tmp_path.iterdir())
    with pytest.raises(private.PrivateFileError):
        private.create_secret(tmp_path / 'secret', 'not bytes')
    assert list(tmp_path.iterdir()) == before


@posix
def test_open_private_file_reads_same_validated_fd_in_readable_parent(tmp_path):
    directory = tmp_path / 'readable'
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    destination = directory / 'session'
    private.create_secret(destination, b'complete credential')
    descriptor = private.open_private_file(destination)
    try:
        # Replacement of the pathname cannot redirect this already-proved read.
        destination.rename(directory / 'original')
        private.create_secret(destination, b'different credential')
        assert os.read(descriptor, 513) == b'complete credential'
        with pytest.raises(OSError):
            os.write(descriptor, b'not writable')
    finally:
        os.close(descriptor)


@posix
def test_open_private_file_distinguishes_missing_from_links_and_privacy_refusals(tmp_path):
    directory = tmp_path / 'readable'
    directory.mkdir(mode=0o755)
    with pytest.raises(private.PrivateFileError) as missing:
        private.open_private_file(directory / 'absent')
    assert missing.value.errno == errno.ENOENT
    destination = directory / 'session'
    private.create_secret(destination, b'secret')
    link = directory / 'alias'
    link.symlink_to(destination)
    with pytest.raises(private.PrivateFileError) as linked:
        private.open_private_file(link)
    assert linked.value.errno != errno.ENOENT
    destination.chmod(0o644)
    with pytest.raises(private.PrivateFileError) as unsafe:
        private.open_private_file(destination)
    assert unsafe.value.errno != errno.ENOENT
    fifo = directory / 'pipe'
    os.mkfifo(fifo, 0o600)
    with pytest.raises(private.PrivateFileError):
        private.open_private_file(fifo)


@posix
def test_open_private_file_closes_fd_when_validation_refuses(tmp_path, monkeypatch):
    directory = tmp_path / 'readable'
    directory.mkdir(mode=0o755)
    destination = directory / 'session'
    private.create_secret(destination, b'secret')
    refused = []

    def reject(descriptor, *, directory):
        refused.append(descriptor)
        private._reject()

    monkeypatch.setattr(private, '_posix_validate', reject)
    with pytest.raises(private.PrivateFileError):
        private.open_private_file(destination)
    assert len(refused) == 1
    with pytest.raises(OSError):
        os.fstat(refused[0])
