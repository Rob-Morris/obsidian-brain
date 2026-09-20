"""Bounded, private transport state for same-process POSIX replacement."""

import base64
from contextlib import contextmanager
import json
import os
import select
import stat
import tempfile


HANDOFF_VERSION = 2
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
def state_descriptor(state: dict):
    encoded = json.dumps(state, separators=(",", ":"), ensure_ascii=True).encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("handoff state exceeds 1 MiB")
    # TemporaryFile is unlinked and mode 0600; only this explicitly inherited fd
    # crosses exec. Never persist authentication or exceptional consent here.
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
    if isinstance(state, dict) and state.get("version") == HANDOFF_VERSION:
        required.update(("subscriptions", "resolution"))
    if not isinstance(state, dict) or set(state) != required:
        raise ValueError("invalid handoff state fields")
    resolution = state.get("resolution")
    if resolution is not None:
        if (not isinstance(resolution, dict) or set(resolution) != {"workspace_env", "vault_root_env", "start_dir"}
                or not isinstance(resolution["start_dir"], str) or not os.path.isabs(resolution["start_dir"])
                or any(value is not None and not isinstance(value, str) for value in resolution.values())):
            raise ValueError("invalid handoff resolution inputs")
    if state["version"] not in (1, HANDOFF_VERSION) or state["pid"] != expected_pid:
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
    remainder = base64.b64decode(state["remainder"], validate=True)
    if len(remainder) > READ_CHUNK:
        raise ValueError("handoff read-ahead exceeds one chunk")
    from ._proxy_session import Subscriptions
    Subscriptions.restore(state.get("subscriptions", []))
    return state
