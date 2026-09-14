"""Private process-owned transport for opaque, bounded application state."""

from __future__ import annotations

import array
import hashlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import shutil
import struct
import tempfile
from threading import RLock, Thread
import uuid

from .file_lock import exclusive_file_lock
from .paths import config_home
from .consent_state import (
    MAX_STATE_BYTES,
    MemoryStateStore,
    StateKeyPage,
    StateSnapshot,
    StoreCapacityError,
    StoreClosedError,
    StoreConflictError,
    json_bytes,
)


PROTOCOL = "brain.owner-state/1"
_HANDSHAKE = PROTOCOL.encode("ascii")
MAX_FRAME_BYTES = 2 * MAX_STATE_BYTES
MAX_CONNECTIONS = 128


class OwnerConnectionError(RuntimeError):
    """Owner communication failed; a submitted update must not be blindly replayed."""


class OwnerTransportUnavailable(RuntimeError):
    """This platform does not support the requested private owner transport."""


@dataclass(frozen=True, slots=True)
class OwnerIdentity:
    context_id: str
    vault_root: str
    private_directory: str
    coordination_path: str


def _read_exact(channel: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        part = channel.recv(size - len(result))
        if not part:
            raise EOFError("owner channel ended")
        result.extend(part)
    return bytes(result)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate owner protocol field")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _read_frame(channel: socket.socket) -> dict:
    size = struct.unpack("!I", _read_exact(channel, 4))[0]
    if not 1 <= size <= MAX_FRAME_BYTES:
        raise ValueError("owner protocol frame exceeds its byte bound")
    value = json.loads(
        _read_exact(channel, size),
        object_pairs_hook=_object,
        parse_constant=_invalid_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("owner protocol frame must be an object")
    return value


def _send_frame(channel: socket.socket, value: dict) -> None:
    encoded = json_bytes(value)
    if len(encoded) > MAX_FRAME_BYTES:
        raise ValueError("owner protocol frame exceeds its byte bound")
    channel.sendall(struct.pack("!I", len(encoded)) + encoded)


def _fields(value: dict, names: set[str]) -> None:
    if set(value) != names:
        raise ValueError("owner protocol fields do not match the operation")


class RemoteStateStore:
    """Use one private stream; channel failure never automatically repeats an update."""

    def __init__(self, channel: socket.socket, *, timeout: float = 30) -> None:
        self._channel = channel
        self._channel.settimeout(timeout)
        self._lock = RLock()
        try:
            hello = _read_frame(channel)
            _fields(hello, {"protocol", "context_id", "vault_root", "private_directory", "coordination_path"})
            if hello["protocol"] != PROTOCOL:
                raise ValueError("owner protocol version is incompatible")
            if any(not isinstance(hello[key], str) or not hello[key] for key in ("context_id", "vault_root", "private_directory", "coordination_path")):
                raise ValueError("owner identity fields must be non-empty strings")
            if any(not Path(hello[key]).is_absolute() for key in ("vault_root", "private_directory", "coordination_path")):
                raise ValueError("owner directories must be absolute")
            self.identity = OwnerIdentity(hello["context_id"], hello["vault_root"], hello["private_directory"], hello["coordination_path"])
        except (OSError, EOFError, ValueError) as exc:
            channel.close()
            raise OwnerConnectionError("private owner connection could not be established") from exc

    @classmethod
    def connect_inherited(cls, fd: int, *, timeout: float = 30) -> RemoteStateStore:
        """Attach using an inherited descriptor, exchanging a private reply-channel handle."""

        if not hasattr(socket.socket, "sendmsg") or not hasattr(socket, "SCM_RIGHTS"):
            raise OwnerTransportUnavailable("inherited CLI job channels are unsupported on this platform")
        if type(fd) is not int or fd < 0:
            raise OwnerConnectionError("owner descriptor locator is invalid")
        client = server = rendezvous = None
        try:
            duplicated_fd = os.dup(fd)
            try:
                rendezvous = socket.socket(fileno=duplicated_fd)
            except OSError:
                os.close(duplicated_fd)
                raise
            if rendezvous.family != socket.AF_UNIX or rendezvous.type != socket.SOCK_DGRAM:
                raise ValueError("owner locator does not name a private datagram channel")
            rendezvous.settimeout(timeout)
            client, server = socket.socketpair()
            rendezvous.sendmsg(
                [_HANDSHAKE],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [server.fileno()]))],
            )
            return cls(client, timeout=timeout)
        except (OSError, ValueError) as exc:
            if client is not None:
                client.close()
            raise OwnerConnectionError("inherited owner channel is unavailable; start or correctly forward a job context") from exc
        finally:
            if server is not None:
                server.close()
            if rendezvous is not None:
                rendezvous.close()

    def snapshot(self, keys: tuple[str, ...]) -> StateSnapshot:
        """Read the requested state keys through the private owner connection."""

        if not isinstance(keys, tuple):
            raise ValueError("snapshot keys must be a tuple of distinct strings")
        result = self._call({"operation": "snapshot", "keys": list(keys)})
        _fields(result, {"values", "versions"})
        values, versions = result["values"], result["versions"]
        if (
            not isinstance(values, dict) or not isinstance(versions, dict)
            or set(versions) != set(keys) or not values.keys() <= versions.keys()
            or any(type(version) is not int or version < 0 for version in versions.values())
        ):
            self.close()
            raise OwnerConnectionError("invalid owner snapshot response")
        return StateSnapshot(values, versions)

    def compare_exchange(self, expected_versions, writes) -> bool:
        """Submit one atomic update; a communication error leaves its outcome uncertain."""

        result = self._call({"operation": "compare_exchange", "expected_versions": expected_versions, "writes": writes})
        _fields(result, {"applied"})
        if type(result["applied"]) is not bool:
            raise OwnerConnectionError("invalid owner comparison result")
        return result["applied"]

    def list_keys(self, *, prefix="", after=None, limit=128, revision=None) -> StateKeyPage:
        """Read a bounded page of live state keys with revision-safe continuation."""

        result = self._call({"operation": "list_keys", "prefix": prefix, "after": after, "limit": limit, "revision": revision})
        _fields(result, {"keys", "next_after", "revision"})
        return StateKeyPage(tuple(result["keys"]), result["next_after"], result["revision"])

    def close(self) -> None:
        """Close this caller's connection without ending the process owner's context."""

        with self._lock:
            self._channel.close()

    def _call(self, payload: dict) -> dict:
        with self._lock:
            try:
                _send_frame(self._channel, payload)
                try:
                    response = _read_frame(self._channel)
                except ValueError as exc:
                    self._channel.close()
                    raise OwnerConnectionError("invalid owner response; update outcome may be unknown") from exc
                if set(response) == {"result"} and isinstance(response["result"], dict):
                    return response["result"]
                if set(response) != {"error", "message"}:
                    self._channel.close()
                    raise OwnerConnectionError("invalid owner response fields")
                errors = {
                    "closed": StoreClosedError,
                    "capacity": StoreCapacityError,
                    "conflict": StoreConflictError,
                    "invalid_request": ValueError,
                }
                error = errors.get(response["error"]) if isinstance(response["error"], str) else None
                if error is None or not isinstance(response["message"], str):
                    self._channel.close()
                    raise OwnerConnectionError("invalid owner error response")
            except (OSError, EOFError) as exc:
                self._channel.close()
                raise OwnerConnectionError("owner channel lost; the submitted operation must not be automatically replayed") from exc
            raise error(response["message"])


class ConsentOwner:
    """Own opaque state and private connections for one pinned Brain root lifetime."""

    def __init__(self, vault_root: Path, *, store: MemoryStateStore | None = None, lock_timeout: float | None = None) -> None:
        root = Path(vault_root)
        if not root.is_absolute():
            raise ValueError("owner root must be absolute")
        self._root = root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("owner root must be a directory")
        stat = self._root.stat()
        self._root_identity = (stat.st_dev, stat.st_ino)
        # This inert per-Brain lock survives owner lifetimes and root replacement.
        # It is coordination only: no grant, secret or owner identity is persisted.
        root_digest = hashlib.sha256(str(self._root).encode("utf-8")).hexdigest()
        self.coordination_path = (config_home() / "brain" / "locks" /
                                  f"owner-pins-{root_digest}.lock").resolve()
        with exclusive_file_lock(self.coordination_path, follow_symlinks=False, timeout=lock_timeout):
            pass
        self.private_directory = Path(tempfile.mkdtemp(prefix="brain-owner-")).resolve()
        self.identity = OwnerIdentity(
            f"context-{uuid.uuid4()}", str(self._root), str(self.private_directory),
            str(self.coordination_path),
        )
        self.store = store if store is not None else MemoryStateStore()
        self._lock = RLock()
        self._closed = False
        self._clients: set[socket.socket] = set()
        self._rendezvous: tuple[socket.socket, socket.socket] | None = None
        self._accept_thread: Thread | None = None

    @property
    def rendezvous_fd(self) -> int:
        """Expose only the descriptor locator for deliberate descendant inheritance."""

        if not hasattr(socket.socket, "sendmsg") or not hasattr(socket, "SCM_RIGHTS"):
            raise OwnerTransportUnavailable("inherited CLI job channels are unsupported on this platform")
        with self._lock:
            self._require_open()
            if self._rendezvous is None:
                self._rendezvous = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
                self._rendezvous[0].settimeout(.2)
                self._accept_thread = Thread(target=self._accept, daemon=True)
                self._accept_thread.start()
            return self._rendezvous[1].fileno()

    def connect(self) -> RemoteStateStore:
        """Create a private local stream, also usable where descriptor passing is absent."""

        client, server = socket.socketpair()
        try:
            self.serve_connection(server)
            return RemoteStateStore(client)
        except Exception:
            client.close()
            server.close()
            raise

    def serve_connection(self, channel: socket.socket) -> None:
        """Take ownership of one trusted private stream and serve bounded state requests."""

        with self._lock:
            if self._closed:
                channel.close()
                raise StoreClosedError("owner context has ended")
            if len(self._clients) >= MAX_CONNECTIONS:
                channel.close()
                raise StoreCapacityError("owner connection capacity reached")
            channel.set_inheritable(False)
            channel.settimeout(None)
            self._clients.add(channel)
            Thread(target=self._serve, args=(channel,), daemon=True).start()

    def close(self, *, lock_timeout: float | None = None) -> None:
        """End admission, destroy state and disconnect all surviving descendants."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.store.close()
            for channel in tuple(self._clients):
                try:
                    channel.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                channel.close()
            if self._rendezvous is not None:
                for channel in self._rendezvous:
                    channel.close()
        # Close the state/channel before waiting for a pin user. A pin user may
        # be waiting for a reply from this owner and must observe shutdown.
        # The application uses this same lock for all private input mutations.
        try:
            with exclusive_file_lock(self.coordination_path,
                                     create_parent=False, follow_symlinks=False, timeout=lock_timeout):
                shutil.rmtree(self.private_directory)
        except FileNotFoundError:
            # Another close or already-completed cleanup cannot revive state.
            pass

    def _require_open(self) -> None:
        if self._closed:
            raise StoreClosedError("owner context has ended")

    def _check_root(self) -> None:
        try:
            stat = self._root.stat()
            matches = not self._root.is_symlink() and self._root.is_dir() and (stat.st_dev, stat.st_ino) == self._root_identity
        except OSError:
            matches = False
        if not matches:
            self.close()
            raise StoreClosedError("selected Brain root changed; start a fresh owner context")

    def _accept(self) -> None:
        receiver = self._rendezvous[0]
        while True:
            try:
                data, ancillary, flags, _address = receiver.recvmsg(64, socket.CMSG_SPACE(8 * array.array("i").itemsize))
            except socket.timeout:
                continue
            except OSError:
                return
            handles = []
            for level, kind, raw in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    values = array.array("i")
                    values.frombytes(raw[:len(raw) - len(raw) % values.itemsize])
                    handles.extend(values)
            if data != _HANDSHAKE or len(handles) != 1 or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                for fd in handles:
                    os.close(fd)
                continue
            try:
                channel = socket.socket(fileno=handles[0])
            except OSError:
                os.close(handles[0])
                continue
            if channel.family != socket.AF_UNIX or channel.type != socket.SOCK_STREAM:
                channel.close()
                continue
            try:
                self.serve_connection(channel)
            except (StoreClosedError, StoreCapacityError):
                channel.close()

    def _serve(self, channel: socket.socket) -> None:
        try:
            _send_frame(channel, {"protocol": PROTOCOL, "context_id": self.identity.context_id, "vault_root": self.identity.vault_root, "private_directory": self.identity.private_directory, "coordination_path": self.identity.coordination_path})
            while True:
                request = _read_frame(channel)
                try:
                    self._check_root()
                    response = {"result": self._dispatch(request)}
                except (StoreClosedError, StoreCapacityError, StoreConflictError, ValueError) as exc:
                    code = "invalid_request"
                    if isinstance(exc, StoreClosedError):
                        code = "closed"
                    elif isinstance(exc, StoreCapacityError):
                        code = "capacity"
                    elif isinstance(exc, StoreConflictError):
                        code = "conflict"
                    response = {"error": code, "message": str(exc)}
                _send_frame(channel, response)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            with self._lock:
                self._clients.discard(channel)
            channel.close()

    def _dispatch(self, request: dict) -> dict:
        operation = request.get("operation")
        if operation == "snapshot":
            _fields(request, {"operation", "keys"})
            if not isinstance(request["keys"], list):
                raise ValueError("snapshot keys must be an array")
            result = self.store.snapshot(tuple(request["keys"]))
            return {"values": result.values, "versions": result.versions}
        if operation == "compare_exchange":
            _fields(request, {"operation", "expected_versions", "writes"})
            return {"applied": self.store.compare_exchange(request["expected_versions"], request["writes"])}
        if operation == "list_keys":
            _fields(request, {"operation", "prefix", "after", "limit", "revision"})
            result = self.store.list_keys(prefix=request["prefix"], after=request["after"], limit=request["limit"], revision=request["revision"])
            return {"keys": list(result.keys), "next_after": result.next_after, "revision": result.revision}
        raise ValueError("unknown owner state operation")
