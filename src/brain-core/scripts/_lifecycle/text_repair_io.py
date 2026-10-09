"""Bounded byte I/O for text repair; callers own candidate authorisation."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import stat

from _portable_path import validate_portable_relative_path


class TextRepairIOError(OSError):
    """A candidate could not be accessed safely (``path`` names the candidate)."""

    def __init__(self, path: str, cause: Exception, *, committed: bool = False):
        self.path = path
        self.committed = committed
        super().__init__(f"{path}: {cause}")


def _paths(root, relative):
    validate_portable_relative_path(relative)
    root = Path(os.path.abspath(os.fspath(root)))
    return root, relative.split("/")


@contextmanager
def _posix_parent(root, parts):
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root.anchor, flags)
        stack.callback(os.close, fd)
        edges = []
        for name in [*root.parts[1:], *parts[:-1]]:
            child = os.open(name, flags, dir_fd=fd)
            stack.callback(os.close, child)
            edges.append((fd, name, os.fstat(child)))
            fd = child
        yield fd, edges


def _parent_current(edges):
    for fd, name, original in edges:
        try:
            current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            return False
    return True


def _posix_read(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("candidate is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _matches(data, expected_digest):
    return hashlib.sha256(data).hexdigest() == expected_digest


def _posix_repair(root, parts, expected_digest, fixedbytes):
    committed = False
    try:
        with _posix_parent(root, parts) as (parent, edges):
            if not _matches(_posix_read(parent, parts[-1]), expected_digest):
                return False
            if not _parent_current(edges):
                return False
            temporary = f"{parts[-1]}.{secrets.token_hex(4)}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                try:
                    with os.fdopen(fd, "wb", closefd=False) as stream:
                        stream.write(fixedbytes)
                        stream.flush()
                        os.fsync(fd)
                finally:
                    os.close(fd)
                if not _parent_current(edges):
                    return False
                if not _matches(_posix_read(parent, parts[-1]), expected_digest):
                    return False
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                committed = True
                if not _parent_current(edges):
                    raise ValueError("committed to original directory but path changed during repair")
                return True
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
    except (OSError, ValueError) as exc:
        if committed:
            raise TextRepairIOError("/".join(parts), exc, committed=True) from exc
        raise


class _WindowsHandles:
    """NT handles and layouts from Microsoft's ntdef/ntifs contracts."""

    DIRECTORY_ACCESS = 0x00100000 | 0x20 | 0x80  # SYNCHRONIZE, TRAVERSE, READ_ATTRIBUTES
    READ_ACCESS = 0x00100000 | 0x1 | 0x80
    STAGE_ACCESS = 0x00100000 | 0x00010000 | 0x2 | 0x80  # DELETE and WRITE_DATA

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll")

        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                        ("Buffer", wintypes.LPWSTR)]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                        ("ObjectName", ctypes.POINTER(UnicodeString)),
                        ("Attributes", wintypes.ULONG),
                        ("SecurityDescriptor", wintypes.LPVOID),
                        ("SecurityQualityOfService", wintypes.LPVOID)]

        class StatusUnion(ctypes.Union):
            _fields_ = [("Status", ctypes.c_int32), ("Pointer", wintypes.LPVOID)]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("Result", StatusUnion), ("Information", ctypes.c_size_t)]

        class RenameUnion(ctypes.Union):
            _fields_ = [("ReplaceIfExists", ctypes.c_ubyte), ("Flags", wintypes.ULONG)]

        class RenameInformation(ctypes.Structure):
            _fields_ = [("Options", RenameUnion), ("RootDirectory", wintypes.HANDLE),
                        ("FileNameLength", wintypes.ULONG), ("FileName", wintypes.WCHAR * 1)]

        class AttributeTagInfo(ctypes.Structure):
            _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

        self.unicode_string = UnicodeString
        self.object_attributes = ObjectAttributes
        self.io_status = IoStatusBlock
        self.rename_info = RenameInformation
        self.attribute_info = AttributeTagInfo
        self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        wintypes.DWORD, wintypes.LPVOID,
                                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE,
                                                        ctypes.c_int, wintypes.LPVOID,
                                                        wintypes.DWORD]
        self.api.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.api.GetFileType.argtypes = [wintypes.HANDLE]
        self.api.GetFileType.restype = wintypes.DWORD
        self.nt.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
                                        ctypes.POINTER(ObjectAttributes),
                                        ctypes.POINTER(IoStatusBlock), wintypes.LPVOID,
                                        wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
                                        wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG]
        self.nt.NtCreateFile.restype = ctypes.c_int32
        self.nt.NtSetInformationFile.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(IoStatusBlock),
                                                wintypes.LPVOID, wintypes.ULONG, ctypes.c_int]
        self.nt.NtSetInformationFile.restype = ctypes.c_int32
        self.nt.RtlNtStatusToDosError.argtypes = [ctypes.c_int32]
        self.nt.RtlNtStatusToDosError.restype = wintypes.ULONG

    def check_status(self, status):
        if status < 0:
            raise self.ctypes.WinError(self.nt.RtlNtStatusToDosError(status))

    def close(self, handle):
        if not self.api.CloseHandle(handle):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def validate(self, handle, *, directory):
        info = self.attribute_info()
        if not self.api.GetFileInformationByHandleEx(
                handle, 9, self.ctypes.byref(info), self.ctypes.sizeof(info)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        if info.FileAttributes & 0x400:
            raise ValueError("refusing reparse point")
        if bool(info.FileAttributes & 0x10) != directory:
            raise ValueError("unexpected file type")
        if not directory and self.api.GetFileType(handle) != 1:
            raise ValueError("candidate is not a disk file")

    def anchor(self, root):
        # Only the filesystem anchor is opened by pathname; descendants use handles.
        handle = self.api.CreateFileW(root.anchor, self.DIRECTORY_ACCESS, 3, None,
                                      3, 0x02000000 | 0x00200000, None)
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            self.validate(handle, directory=True)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def open(self, parent, name, *, directory=False, create=False):
        from ctypes import wintypes
        validate_portable_relative_path(name)
        if "/" in name:
            raise ValueError("native open requires one component")
        encoded = name.encode("utf-16-le")
        if len(encoded) > 65532:
            raise ValueError("native filename is too long")
        buffer = self.ctypes.create_unicode_buffer(name)
        text = self.unicode_string(len(encoded), len(encoded) + 2,
                                   self.ctypes.cast(buffer, wintypes.LPWSTR))
        attrs = self.object_attributes(self.ctypes.sizeof(self.object_attributes), parent,
                                      self.ctypes.pointer(text), 0x40 | 0x1000, None, None)
        handle = wintypes.HANDLE()
        status = self.io_status()
        access = self.DIRECTORY_ACCESS if directory else self.STAGE_ACCESS if create else self.READ_ACCESS
        options = 0x00200000 | 0x20 | (0x1 if directory else 0x40)
        self.check_status(self.nt.NtCreateFile(self.ctypes.byref(handle), access,
                          self.ctypes.byref(attrs), self.ctypes.byref(status), None,
                          0x80, 3, 2 if create else 1, options, None, 0))
        try:
            self.validate(handle.value, directory=directory)
            return handle.value
        except BaseException:
            if create:
                try:
                    self.delete(handle.value)
                finally:
                    self.close(handle.value)
            else:
                self.close(handle.value)
            raise

    def rename(self, handle, parent, name):
        validate_portable_relative_path(name)
        if "/" in name:
            raise ValueError("native rename requires one component")
        encoded = name.encode("utf-16-le")
        offset = self.rename_info.FileName.offset
        buffer = self.ctypes.create_string_buffer(self.ctypes.sizeof(self.rename_info) + len(encoded))
        info = self.rename_info.from_buffer(buffer)
        info.Options.ReplaceIfExists = 1
        info.RootDirectory = parent
        info.FileNameLength = len(encoded)
        self.ctypes.memmove(self.ctypes.addressof(buffer) + offset, encoded, len(encoded))
        status = self.io_status()
        self.check_status(self.nt.NtSetInformationFile(handle, self.ctypes.byref(status),
                          buffer, len(buffer), 10))

    def delete(self, handle):
        # FileDispositionInformation: delete this exact staging object on close.
        flag = self.ctypes.c_ubyte(1)
        status = self.io_status()
        self.check_status(self.nt.NtSetInformationFile(handle, self.ctypes.byref(status),
                          self.ctypes.byref(flag), self.ctypes.sizeof(flag), 13))


@contextmanager
def _windows_parent(root, parts):
    native = _WindowsHandles()
    with ExitStack() as stack:
        parent = native.anchor(root)
        stack.callback(native.close, parent)
        directories = [parent]
        for name in [*root.parts[1:], *parts[:-1]]:
            parent = native.open(parent, name, directory=True)
            stack.callback(native.close, parent)
            directories.append(parent)
        yield native, parent, directories


@contextmanager
def _windows_fd(native, handle, *, write=False):
    import msvcrt
    try:
        fd = msvcrt.open_osfhandle(handle, (os.O_WRONLY if write else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        try:
            if write:
                native.delete(handle)
        finally:
            native.close(handle)
        raise
    # Only the CRT closes the handle after ownership has transferred.
    try:
        yield fd
    finally:
        os.close(fd)


def _windows_read_fd(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    with os.fdopen(fd, "rb", closefd=False) as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("candidate is not a regular file")
        return stream.read()


def _windows_validate_directories(native, directories):
    for handle in directories:
        native.validate(handle, directory=True)


def _windows_repair(root, parts, expected_digest, fixedbytes):
    committed = False
    try:
        with _windows_parent(root, parts) as (native, parent, directories):
            with ExitStack() as original:
                target = native.open(parent, parts[-1])
                target_fd = original.enter_context(_windows_fd(native, target))
                _windows_validate_directories(native, directories)
                if not _matches(_windows_read_fd(target_fd), expected_digest):
                    return False
                temporary = f"{parts[-1]}.{secrets.token_hex(4)}.tmp"
                stage = native.open(parent, temporary, create=True)
                # The target remains locked until the stage exists. The stage
                # then keeps the parent non-empty even after the target closes.
                with _windows_fd(native, stage, write=True) as stage_fd:
                    try:
                        _windows_validate_directories(native, directories)
                        with os.fdopen(stage_fd, "wb", closefd=False) as stream:
                            stream.write(fixedbytes)
                            stream.flush()
                            os.fsync(stage_fd)
                        if not _matches(_windows_read_fd(target_fd), expected_digest):
                            return False
                        native.validate(stage, directory=False)
                        _windows_validate_directories(native, directories)
                        original.close()
                        native.rename(stage, parent, parts[-1])
                        committed = True
                        return True
                    finally:
                        if not committed:
                            native.delete(stage)
    except (OSError, ValueError) as exc:
        if committed:
            raise TextRepairIOError("/".join(parts), exc, committed=True) from exc
        raise


def read_candidate(root: str | Path, relative: str) -> bytes:
    """Read a regular candidate without following root, parent or file links."""
    try:
        root, parts = _paths(root, relative)
        if os.name == "posix":
            with _posix_parent(root, parts) as (parent, _):
                return _posix_read(parent, parts[-1])
        if os.name == "nt":
            with _windows_parent(root, parts) as (native, parent, directories):
                with _windows_fd(native, native.open(parent, parts[-1])) as fd:
                    _windows_validate_directories(native, directories)
                    return _windows_read_fd(fd)
        raise ValueError(f"unsupported platform: {os.name}")
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise TextRepairIOError(relative, exc) from exc


def repair_candidate(root: str | Path, relative: str, expected_digest: str,
                     fixedbytes: bytes) -> bool:
    """Replace matching SHA-256 bytes atomically; skip missing/changed subjects.

    Parent descriptors bound POSIX writes even if a directory is renamed after
    validation. Windows uses directory-relative NT opens and handle-based rename.
    Post-commit failures carry committed=True; False means no target replacement.
    This is not a compare-and-swap against concurrent edits of the final file.
    """
    try:
        root, parts = _paths(root, relative)
        if os.name == "posix":
            return _posix_repair(root, parts, expected_digest, fixedbytes)
        if os.name == "nt":
            return _windows_repair(root, parts, expected_digest, fixedbytes)
        raise ValueError(f"unsupported platform: {os.name}")
    except TextRepairIOError:
        raise
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        raise TextRepairIOError(relative, exc) from exc
