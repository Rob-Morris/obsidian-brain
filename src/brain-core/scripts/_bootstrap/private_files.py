"""Private local files: POSIX ownership/modes and native Windows owner/DACL proof.

Only protect_dir may change an existing object's permissions. Validators never
repair caller-selected files. Windows permits the current user, SYSTEM and
Administrators, matching mkdir(0700)'s Windows trust boundary. No subprocesses
or optional packages participate in that boundary.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import errno
from functools import lru_cache, wraps
import os
from pathlib import Path
import secrets
import stat


class PrivateFileError(OSError):
    """Content-safe refusal to create or validate a private filesystem object."""


def _reject():
    raise PrivateFileError(errno.EACCES, 'Private filesystem ownership or access could not be verified.')


def _safe_errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PrivateFileError:
            raise
        except OSError as exc:
            code = errno.EEXIST if isinstance(exc, FileExistsError) else errno.ENOENT if isinstance(exc, FileNotFoundError) else errno.EACCES
            raise PrivateFileError(code, 'Private filesystem operation could not be completed.') from None
    return call


def _acl_is_private(owner: str, user: str, aces: list[tuple[int, str]] | None) -> bool:
    """Accept only ordinary allow/deny ACEs with no untrusted allow principal.

    Denies cannot grant access. Unknown/object/callback ACEs are not interpreted
    as ordinary ACEs. Inherit-only allows are checked too: directory privacy
    must also hold for newly created children. An empty DACL grants no access.
    """
    trusted = {user, 'S-1-5-18', 'S-1-5-32-544'}
    return (owner in trusted and aces is not None
            and all(kind == 1 or (kind == 0 and sid in trusted) for kind, sid in aces))


@contextmanager
def _posix_directory(path: Path):
    # Pin each ancestor; openat prevents following a replaced path component.
    with ExitStack() as stack:
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        for component in path.parts[1:]:
            descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        yield descriptor


def _posix_validate(descriptor: int, *, directory: bool):
    info = os.fstat(descriptor)
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_type or info.st_uid != os.getuid() or info.st_mode & 0o077:
        _reject()


@lru_cache(maxsize=1)
def _windows():
    return _WindowsPrivateFiles()


@_safe_errors
def create_dir(path: str | Path) -> None:
    """Create one private directory atomically; never adopt or repair an existing name.

    The parent must exist. EEXIST is preserved for deliberate caller selection
    followed by validate_existing_dir; a racing creator is never trusted here.
    Windows receives the restrictive DACL in CreateDirectoryW itself.
    """
    path = Path(os.path.abspath(path))
    if os.name == 'nt':
        _windows().create_dir(path)
        return
    with _posix_directory(path.parent) as parent:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            _posix_validate(descriptor, directory=True)
        finally:
            os.close(descriptor)


@_safe_errors
def protect_dir(path: str | Path) -> None:
    """Create or secure one server-managed directory; its parent must exist.

    Reject links/reparse points and foreign owners before changing permissions.
    This is deliberately not recursive creation or repair of arbitrary content.
    """
    path = Path(os.path.abspath(path))
    if os.name == 'nt':
        _windows().protect_dir(path)
        return
    with _posix_directory(path.parent) as parent:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if os.fstat(descriptor).st_uid != os.getuid():
                _reject()
            os.fchmod(descriptor, 0o700)
            _posix_validate(descriptor, directory=True)
        finally:
            os.close(descriptor)


@_safe_errors
def validate_existing_dir(path: str | Path) -> None:
    """Validate one existing private directory without changing its permissions.

    Its path must contain no links/reparse points. Ancestors are pinned during
    validation, but their access policy is not rewritten or required private.
    """
    path = Path(os.path.abspath(path))
    if os.name == 'nt':
        with _windows().directory(path) as handle:
            _windows().validate(handle, directory=True)
    else:
        with _posix_directory(path) as descriptor:
            _posix_validate(descriptor, directory=True)


@_safe_errors
def open_private_file(path: str | Path) -> int:
    """Return a validated read-only fd opened without following links/reparse points.

    The caller owns and closes the returned fd and reads through that same fd.
    Missing paths report PrivateFileError.errno == ENOENT; privacy refusals do
    not. Readable caller-selected parent directories remain supported.
    """
    path = Path(os.path.abspath(path))
    if os.name == 'nt':
        return _windows().open_private_file(path)
    with _posix_directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            _posix_validate(descriptor, directory=False)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise


@_safe_errors
def validate_fd(fd: int) -> None:
    """Prove an opened regular file private before reading its sensitive bytes.

    The caller retains fd ownership and must preserve its no-follow/path checks:
    this proves the opened object, not the original pathname used to obtain it.
    Hardlinks share the same ownership and access policy and are permitted.
    Reparse handles and unavailable security information refuse.
    """
    if os.name == 'nt':
        import msvcrt
        _windows().validate(msvcrt.get_osfhandle(fd), directory=False)
    else:
        _posix_validate(fd, directory=False)


def _write_all(descriptor: int, data: bytes):
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError('Incomplete private file write')
        remaining = remaining[written:]
    os.fsync(descriptor)


@_safe_errors
def create_secret(path: str | Path, data: bytes) -> None:
    """Atomically publish private bytes without overwriting an existing name.

    The parent must exist, with no links/reparse points in its path. A restrictive
    staged inode is verified before its first byte is written; linking it publishes
    without replacement.
    Failures remove the stage, never an existing destination. No raw data/path
    is included in exceptions. The caller owns destination retention/cleanup.
    """
    if not isinstance(data, bytes):
        raise PrivateFileError(errno.EINVAL, 'Private file content must be bytes.')
    path = Path(os.path.abspath(path))
    temporary = '.brain-private-' + secrets.token_hex(16)
    if os.name == 'nt':
        _windows().create_secret(path, temporary, data)
        return
    with _posix_directory(path.parent) as parent:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            try:
                _posix_validate(descriptor, directory=False)
                _write_all(descriptor, data)
            finally:
                os.close(descriptor)
            os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)


class _WindowsPrivateFiles:
    """Thin Win32 binding; ACL policy above remains platform-independent."""

    READ_CONTROL = 0x20000
    WRITE_DAC = 0x40000
    FILE_READ_ATTRIBUTES = 0x80
    OWNER_SECURITY_INFORMATION = 0x1
    DACL_SECURITY_INFORMATION = 0x4
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    SE_FILE_OBJECT = 1
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    OPEN_EXISTING = 3
    CREATE_NEW = 1
    SHARE_READ_WRITE = 3

    def __init__(self):
        """Bind pointer-width-safe APIs and obtain the process token's user SID."""
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.security = c.WinDLL('advapi32', use_last_error=True)
        pointer = w.LPVOID

        class Attributes(c.Structure):
            _fields_ = [('length', w.DWORD), ('descriptor', pointer), ('inherit', w.BOOL)]

        class FileInfo(c.Structure):
            _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME), ('accessed', w.FILETIME),
                        ('written', w.FILETIME), ('volume', w.DWORD), ('size_high', w.DWORD),
                        ('size_low', w.DWORD), ('links', w.DWORD), ('index_high', w.DWORD), ('index_low', w.DWORD)]

        class Acl(c.Structure):
            _fields_ = [('revision', w.BYTE), ('reserved', w.BYTE), ('size', w.WORD),
                        ('count', w.WORD), ('reserved2', w.WORD)]

        class Ace(c.Structure):
            _fields_ = [('kind', w.BYTE), ('flags', w.BYTE), ('size', w.WORD)]

        self.Attributes, self.FileInfo, self.Acl, self.Ace = Attributes, FileInfo, Acl, Ace
        signatures = (
            (self.kernel, 'CreateFileW', [w.LPCWSTR, w.DWORD, w.DWORD, pointer, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            (self.kernel, 'CreateDirectoryW', [w.LPCWSTR, pointer], w.BOOL),
            (self.kernel, 'CloseHandle', [w.HANDLE], w.BOOL),
            (self.kernel, 'GetCurrentProcess', [], w.HANDLE),
            (self.kernel, 'GetFileType', [w.HANDLE], w.DWORD),
            (self.kernel, 'GetFileInformationByHandle', [w.HANDLE, c.POINTER(FileInfo)], w.BOOL),
            (self.kernel, 'LocalFree', [pointer], pointer),
            (self.security, 'OpenProcessToken', [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
            (self.security, 'GetTokenInformation', [w.HANDLE, c.c_int, pointer, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            (self.security, 'ConvertSidToStringSidW', [pointer, c.POINTER(w.LPWSTR)], w.BOOL),
            (self.security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', [w.LPCWSTR, w.DWORD, c.POINTER(pointer), pointer], w.BOOL),
            (self.security, 'GetSecurityInfo', [w.HANDLE, c.c_int, w.DWORD, c.POINTER(pointer), pointer, c.POINTER(pointer), pointer, c.POINTER(pointer)], w.DWORD),
            (self.security, 'SetSecurityInfo', [w.HANDLE, c.c_int, w.DWORD, pointer, pointer, pointer, pointer], w.DWORD),
            (self.security, 'GetSecurityDescriptorDacl', [pointer, c.POINTER(w.BOOL), c.POINTER(pointer), c.POINTER(w.BOOL)], w.BOOL),
            (self.security, 'GetAce', [pointer, w.DWORD, c.POINTER(pointer)], w.BOOL),
            (self.security, 'IsValidSid', [pointer], w.BOOL),
            (self.security, 'GetLengthSid', [pointer], w.DWORD),
        )
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        self.user = self._current_user()

    def _check(self, success):
        if not success:
            _reject()

    def _sid(self, pointer):
        from ctypes import wintypes as w
        self._check(pointer and self.security.IsValidSid(pointer))
        text = w.LPWSTR()
        self._check(self.security.ConvertSidToStringSidW(pointer, self.c.byref(text)))
        try:
            return text.value
        finally:
            self.kernel.LocalFree(self.c.cast(text, w.LPVOID))

    def _current_user(self):
        from ctypes import wintypes as w
        token, length = w.HANDLE(), w.DWORD()
        self._check(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 0x8, self.c.byref(token)))
        try:
            self.security.GetTokenInformation(token, 1, None, 0, self.c.byref(length))
            self._check(length.value and length.value <= 65536)
            data = self.c.create_string_buffer(length.value)
            self._check(self.security.GetTokenInformation(token, 1, data, length, self.c.byref(length)))
            return self._sid(self.c.cast(data, self.c.POINTER(w.LPVOID))[0])
        finally:
            self.kernel.CloseHandle(token)

    @contextmanager
    def _descriptor(self, *, directory):
        from ctypes import wintypes as w
        inherit = 'OICI' if directory else ''
        sddl = f'O:{self.user}D:P' + ''.join(f'(A;{inherit};FA;;;{sid})'
                                            for sid in (self.user, 'SY', 'BA'))
        descriptor = w.LPVOID()
        self._check(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, self.c.byref(descriptor), None))
        try:
            yield descriptor
        finally:
            self.kernel.LocalFree(descriptor)

    def _kind(self, handle, *, directory):
        info = self.FileInfo()
        self._check(self.kernel.GetFileType(handle) == 1)
        self._check(self.kernel.GetFileInformationByHandle(handle, self.c.byref(info)))
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            _reject()

    @contextmanager
    def _open(self, path, *, directory, access=READ_CONTROL | FILE_READ_ATTRIBUTES):
        handle = self._open_handle(path, directory=directory, access=access)
        try:
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    def _open_handle(self, path, *, directory, access):
        handle = self.kernel.CreateFileW(str(path), access, self.SHARE_READ_WRITE, None,
            self.OPEN_EXISTING, self.FILE_FLAG_BACKUP_SEMANTICS | self.FILE_FLAG_OPEN_REPARSE_POINT, None)
        if handle == self.c.c_void_p(-1).value:
            if self.c.get_last_error() in (2, 3):  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
                raise FileNotFoundError(errno.ENOENT, 'Private filesystem object is missing.')
            _reject()
        try:
            self._kind(handle, directory=directory)
            return handle
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def open_private_file(self, path):
        import msvcrt
        with self._parents(path):
            handle = self._open_handle(path, directory=False,
                access=0x80000000 | self.READ_CONTROL | self.FILE_READ_ATTRIBUTES)
            try:
                self.validate(handle, directory=False)
                return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
            except BaseException:
                self.kernel.CloseHandle(handle)
                raise

    @contextmanager
    def _parents(self, path):
        with ExitStack() as stack:
            for parent in reversed(path.parents):
                stack.enter_context(self._open(parent, directory=True, access=self.FILE_READ_ATTRIBUTES))
            yield

    @contextmanager
    def directory(self, path):
        with self._parents(path), self._open(path, directory=True) as handle:
            yield handle

    @contextmanager
    def _security(self, handle):
        from ctypes import wintypes as w
        owner, dacl, descriptor = w.LPVOID(), w.LPVOID(), w.LPVOID()
        self._check(self.security.GetSecurityInfo(handle, self.SE_FILE_OBJECT,
            self.OWNER_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION, self.c.byref(owner),
            None, self.c.byref(dacl), None, self.c.byref(descriptor)) == 0)
        try:
            yield self._sid(owner), dacl
        finally:
            self.kernel.LocalFree(descriptor)

    def _owner_aces(self, handle):
        """Read owner and ACEs once for validation and managed protection.

        Unknown layouts remain unsafe policy data; they are never decoded as
        ordinary allow/deny entries with an accidentally trusted SID.
        """
        from ctypes import wintypes as w
        with self._security(handle) as (owner, dacl):
            aces = None
            if dacl:
                acl = self.c.cast(dacl, self.c.POINTER(self.Acl)).contents
                aces = []
                for index in range(acl.count):
                    entry = w.LPVOID()
                    self._check(self.security.GetAce(dacl, index, self.c.byref(entry)))
                    header = self.c.cast(entry, self.c.POINTER(self.Ace)).contents
                    if header.kind not in (0, 1) or header.size < 16:
                        aces.append((-1, ''))
                        continue
                    sid = entry.value + 8
                    self._check(self.security.IsValidSid(sid))
                    self._check(self.security.GetLengthSid(sid) <= header.size - 8)
                    aces.append((header.kind, self._sid(sid)))
            return owner, aces

    def validate(self, handle, *, directory):
        self._kind(handle, directory=directory)
        owner, aces = self._owner_aces(handle)
        if not _acl_is_private(owner, self.user, aces):
            _reject()

    def protect_dir(self, path):
        from ctypes import wintypes as w
        with self._parents(path), self._descriptor(directory=True) as descriptor:
            attributes = self.Attributes(self.c.sizeof(self.Attributes), descriptor, False)
            if not self.kernel.CreateDirectoryW(str(path), self.c.byref(attributes)):
                self._check(self.c.get_last_error() == 183)
            with self._open(path, directory=True,
                            access=self.READ_CONTROL | self.WRITE_DAC | self.FILE_READ_ATTRIBUTES) as handle:
                owner, aces = self._owner_aces(handle)
                if owner not in {self.user, 'S-1-5-18', 'S-1-5-32-544'}:
                    _reject()
                if _acl_is_private(owner, self.user, aces):
                    return
                present, defaulted, dacl = w.BOOL(), w.BOOL(), w.LPVOID()
                self._check(self.security.GetSecurityDescriptorDacl(descriptor, self.c.byref(present),
                    self.c.byref(dacl), self.c.byref(defaulted)))
                self._check(present and dacl)
                self._check(self.security.SetSecurityInfo(handle, self.SE_FILE_OBJECT,
                    self.DACL_SECURITY_INFORMATION | self.PROTECTED_DACL_SECURITY_INFORMATION,
                    None, None, dacl, None) == 0)
                self.validate(handle, directory=True)

    def create_dir(self, path):
        with self._parents(path), self._descriptor(directory=True) as descriptor:
            attributes = self.Attributes(self.c.sizeof(self.Attributes), descriptor, False)
            if not self.kernel.CreateDirectoryW(str(path), self.c.byref(attributes)):
                if self.c.get_last_error() == 183:
                    raise FileExistsError(errno.EEXIST, 'Private directory already exists.')
                _reject()
            with self._open(path, directory=True) as handle:
                self.validate(handle, directory=True)

    def create_secret(self, path, temporary, data):
        import msvcrt
        stage = path.parent / temporary
        with self._parents(path), self._descriptor(directory=False) as descriptor:
            attributes = self.Attributes(self.c.sizeof(self.Attributes), descriptor, False)
            # GENERIC_WRITE plus security/attribute reads permits verification
            # of this newly created object before handing the handle to the CRT.
            handle = self.kernel.CreateFileW(str(stage), 0x40000000 | self.READ_CONTROL | self.FILE_READ_ATTRIBUTES,
                0, self.c.byref(attributes), self.CREATE_NEW, self.FILE_FLAG_OPEN_REPARSE_POINT, None)
            self._check(handle != self.c.c_void_p(-1).value)
            try:
                try:
                    self.validate(handle, directory=False)
                    descriptor_fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY | os.O_NOINHERIT)
                    handle = None  # The CRT descriptor now owns the native handle.
                    try:
                        _write_all(descriptor_fd, data)
                    finally:
                        os.close(descriptor_fd)
                finally:
                    if handle is not None:
                        self.kernel.CloseHandle(handle)
                os.link(stage, path)
            finally:
                stage.unlink()
