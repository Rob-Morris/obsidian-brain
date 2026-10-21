"""Bounded, private transport state for same-process POSIX replacement."""

import base64
from contextlib import contextmanager
import errno
import json
import os
import select
import socket
import stat
import tempfile


HANDOFF_VERSION = 3
READ_CHUNK = 64 * 1024
MAX_STATE_BYTES = 1024 * 1024
HANDOFF_TIMEOUT = 5.0


class RawLineReader:
    """Own read-ahead explicitly so exec cannot discard Python's hidden buffer."""

    def __init__(self, fd: int, remainder: bytes = b""):
        self.fd = fd
        self.remainder = remainder
        self.eof = False

    @property
    def line_ready(self) -> bool:
        return b"\n" in self.remainder

    def readline(self) -> bytes:
        chunks = []
        while True:
            head, separator, tail = self.remainder.partition(b"\n")
            if separator:
                self.remainder = tail
                return b"".join((*chunks, head, separator))
            chunks.append(self.remainder)
            self.remainder = os.read(self.fd, READ_CHUNK)
            if not self.remainder:
                return b"".join(chunks)

    def readline_interruptible(self, wake_fd: int) -> bytes | None:
        """Keep partial input owned while lifecycle completion wakes the reader."""
        while True:
            if self.line_ready:
                head, _, self.remainder = self.remainder.partition(b"\n")
                return head + b"\n"
            if self.eof:
                tail, self.remainder = self.remainder, b""
                return tail
            ready = select.select([self.fd, wake_fd], [], [])[0]
            # Observe disconnection before handing ownership to a new image.
            # Any bytes read here remain owned by this reader across wakeup.
            if self.fd in ready:
                data = os.read(self.fd, READ_CHUNK)
                if not data:
                    self.eof = True
                    continue
                self.remainder += data
            if self.line_ready:
                head, _, self.remainder = self.remainder.partition(b"\n")
                return head + b"\n"
            if wake_fd in ready:
                os.read(wake_fd, READ_CHUNK)
                return None


def public_session(response: dict) -> dict:
    """Exclude implementation metadata from the negotiated public contract."""
    result = response.get("result", {})
    capabilities = dict(result.get("capabilities", {}))
    experimental = dict(capabilities.get("experimental", {}))
    experimental.pop("brainCommandInterface", None)
    if experimental:
        capabilities["experimental"] = experimental
    else:
        capabilities.pop("experimental", None)
    return {"protocolVersion": result.get("protocolVersion"),
            "supportedVersions": result.get("supportedVersions"), "capabilities": capabilities}


@contextmanager
def _input_channel(data: bytes):
    """Keep read-ahead in an unnamed kernel socket, never a temporary file."""
    if not isinstance(data, bytes) or len(data) > READ_CHUNK:
        raise ValueError("handoff read-ahead exceeds one chunk or is invalid")
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        writer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, READ_CHUNK * 2)
        reader.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, READ_CHUNK * 2)
        if (writer.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF) < len(data)
                or reader.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) < len(data)):
            raise ValueError("handoff input channel capacity unavailable")
        writer.setblocking(False)
        pending = memoryview(data)
        while pending:
            sent = writer.send(pending)
            if not sent:
                raise ValueError("handoff input channel capacity unavailable")
            pending = pending[sent:]
        writer.shutdown(socket.SHUT_WR)
        writer.close()
        reader.setblocking(False)
        reader.set_inheritable(True)
    except OSError:
        reader.close()
        writer.close()
        raise ValueError("handoff input channel unavailable") from None
    except BaseException:
        reader.close()
        writer.close()
        raise
    try:
        yield {"fd": reader.fileno(), "bytes": len(data)}
    finally:
        reader.close()
        writer.close()


@contextmanager
def _validated_input(channel):
    """Validate an inherited unnamed, owned stream socket without consuming it."""
    if (not isinstance(channel, dict) or set(channel) != {"fd", "bytes"}
            or type(channel["fd"]) is not int or channel["fd"] < 3
            or type(channel["bytes"]) is not int or not 0 <= channel["bytes"] <= READ_CHUNK):
        raise ValueError("invalid handoff input descriptor")
    stream = None
    try:
        info = os.fstat(channel["fd"])
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("invalid handoff input descriptor")
        stream = socket.socket(fileno=os.dup(channel["fd"]))
        if (stream.family != socket.AF_UNIX
                or stream.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
                or stream.getsockname() not in ("", b"")):
            raise ValueError("invalid handoff input descriptor")
        try:
            peer = stream.getpeername()
        except OSError as exc:
            # Darwin forgets the peer name after the socketpair writer closes.
            # The bounded peek below still rejects unconnected/listening sockets.
            if exc.errno not in (errno.EINVAL, errno.ENOTCONN):
                raise
        else:
            if peer not in ("", b""):
                raise ValueError("invalid handoff input descriptor")
        stream.setblocking(False)
        if len(stream.recv(READ_CHUNK + 1, socket.MSG_PEEK)) != channel["bytes"]:
            raise ValueError("invalid handoff input byte count")
        yield stream
    except OSError:
        raise ValueError("invalid handoff input descriptor") from None
    finally:
        if stream is not None:
            stream.close()


def consume_remainder(state: dict) -> bytes:
    """Consume transferred input once; v1/v2 remain readable during installed upgrades."""
    value = state["remainder"]
    if isinstance(value, bytes):
        if len(value) > READ_CHUNK:
            raise ValueError("handoff read-ahead exceeds one chunk")
        return value
    if state["version"] in (1, 2):
        data = base64.b64decode(value, validate=True)
        if len(data) > READ_CHUNK:
            raise ValueError("handoff read-ahead exceeds one chunk")
        return data
    with _validated_input(value) as stream:
        try:
            chunks = []
            count = 0
            while True:
                chunk = stream.recv(READ_CHUNK + 1 - count)
                if not chunk:
                    break
                chunks.append(chunk)
                count += len(chunk)
                if count > value["bytes"]:
                    raise ValueError("invalid handoff input byte count")
            if count != value["bytes"]:
                raise ValueError("invalid handoff input byte count")
            return b"".join(chunks)
        finally:
            os.close(value["fd"])


@contextmanager
def state_descriptor(state: dict):
    if state.get("version") != HANDOFF_VERSION:
        raise ValueError("only v3 handoff emission is supported")
    with _input_channel(state["remainder"]) as channel:
        wire_state = {**state, "remainder": channel}
        encoded = json.dumps(wire_state, separators=(",", ":"), ensure_ascii=True).encode()
        if len(encoded) > MAX_STATE_BYTES:
            raise ValueError("handoff state exceeds 1 MiB")
        # Only descriptor metadata enters this private file; raw input remains
        # in the inherited kernel socket, including during preflight.
        with tempfile.TemporaryFile() as stream:
            stream.write(encoded)
            stream.flush()
            os.set_inheritable(stream.fileno(), True)
            yield stream.fileno()


def read_state(fd: int, *, expected_pid: int) -> dict:
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid() or info.st_nlink != 0
            or not 0 < info.st_size <= MAX_STATE_BYTES):
        raise ValueError("handoff descriptor is not a bounded private unlinked file")
    state = json.loads(os.pread(fd, MAX_STATE_BYTES + 1, 0))
    required = {"version", "pid", "vault", "workspace", "python", "server", "protocol",
                "initialise_request", "initialise_response", "public_session", "tools",
                "generation", "request_id", "remainder"}
    if isinstance(state, dict) and state.get("version") in (2, HANDOFF_VERSION):
        required.update(("subscriptions", "resolution"))
    if not isinstance(state, dict) or set(state) != required:
        raise ValueError("invalid handoff state fields")
    resolution = state.get("resolution")
    if resolution is not None:
        if (not isinstance(resolution, dict) or set(resolution) != {"workspace_env", "vault_root_env", "start_dir"}
                or not isinstance(resolution["start_dir"], str) or not os.path.isabs(resolution["start_dir"])
                or any(value is not None and not isinstance(value, str) for value in resolution.values())):
            raise ValueError("invalid handoff resolution inputs")
    if (type(state["version"]) is not int or state["version"] not in (1, 2, HANDOFF_VERSION)
            or type(state["pid"]) is not int or state["pid"] != expected_pid):
        raise ValueError("handoff version or process identity mismatch")
    if state["protocol"] not in {"legacy", "modern"}:
        raise ValueError("handoff requires an established public protocol")
    for key in ("vault", "python"):
        if not isinstance(state[key], str) or not os.path.isabs(state[key]):
            raise ValueError(f"handoff {key} must be absolute")
    if state["server"] != "brain_mcp.server":
        raise ValueError("handoff only supports the installed Brain server")
    if state["workspace"] is not None and (not isinstance(state["workspace"], str) or not os.path.isabs(state["workspace"])):
        raise ValueError("handoff workspace must be absolute")
    if type(state["generation"]) is not int or state["generation"] < 0:
        raise ValueError("invalid handoff generation")
    if type(state["request_id"]) not in (str, int) or state["request_id"] == "":
        raise ValueError("invalid handoff request ID")
    if not isinstance(state["tools"], dict) or not isinstance(state["public_session"], dict):
        raise ValueError("invalid handoff public session")
    if state["protocol"] == "legacy" and not all(isinstance(state[key], dict) for key in ("initialise_request", "initialise_response")):
        raise ValueError("legacy handoff requires negotiated initialisation")
    if state["version"] == HANDOFF_VERSION:
        with _validated_input(state["remainder"]):
            pass
    else:
        remainder = base64.b64decode(state["remainder"], validate=True)
        if len(remainder) > READ_CHUNK:
            raise ValueError("handoff read-ahead exceeds one chunk")
    from ._proxy_session import Subscriptions
    Subscriptions.restore(state.get("subscriptions", []))
    return state
