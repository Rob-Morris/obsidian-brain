"""Deliberate private owner inheritance at trusted process boundaries only."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
from threading import RLock

from .consent_owner import (
    ConsentOwner, OwnerConnectionError, OwnerTransportUnavailable, PROTOCOL,
    RemoteStateStore,
)


OWNER_CHANNEL_ENV = "BRAIN_OWNER_CHANNEL"
OWNER_UNAVAILABLE_ENV = "BRAIN_OWNER_UNAVAILABLE"
PROCESS_CONTEXT_ENV = "BRAIN_PROCESS_CONTEXT"
OWNER_UNAVAILABLE_REASONS = {
    "platform": "Private process owner inheritance is unsupported on this platform.",
    "storage": "Private consent state could not be initialised; repair local runtime storage and restart the session.",
}
_LOCATOR = re.compile(re.escape(PROTOCOL) + r":(stream|job):([0-9]{1,10})\Z")


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Trusted launch attribution; its public identifier is never an attach token."""

    kind: str
    context_id: str

    def __post_init__(self):
        if self.kind not in {"mcp-instance", "cli-job"}:
            raise ValueError("invalid owned process kind")
        if not isinstance(self.context_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.context_id):
            raise ValueError("invalid process context identifier")

    @property
    def source(self) -> str:
        return "host-request" if self.kind == "mcp-instance" else "cli-request"

    def launch_value(self, *, initialise_owner: bool = False) -> str:
        """Encode bounded private launch facts, distinct from request metadata."""
        if type(initialise_owner) is not bool:
            raise ValueError("owner initialisation marker must be Boolean")
        return json.dumps({"schema": "brain.process-context/1", "kind": self.kind,
                           "context_id": self.context_id, "initialise_owner": initialise_owner}, separators=(",", ":"))


def capture_process_identity(environ=None) -> tuple[ProcessIdentity | None, bool]:
    """Consume trusted launcher attribution before application composition."""
    source = os.environ if environ is None else environ
    raw = source.pop(PROCESS_CONTEXT_ENV, None)
    if raw is None:
        return None, False
    try:
        if len(raw) > 512:
            raise ValueError("process context exceeds bound")
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"schema", "kind", "context_id", "initialise_owner"}
                or value["schema"] != "brain.process-context/1" or type(value["initialise_owner"]) is not bool):
            raise ValueError("invalid process context fields")
        return ProcessIdentity(value["kind"], value["context_id"]), value["initialise_owner"]
    except (TypeError, ValueError) as exc:
        raise OwnerConnectionError("private process context is invalid") from exc


def without_owner_environment(environ=None) -> dict[str, str]:
    """Copy the environment without an unrelated process's descriptor locator."""
    result = dict(os.environ if environ is None else environ)
    result.pop(OWNER_CHANNEL_ENV, None)
    result.pop(OWNER_UNAVAILABLE_ENV, None)
    result.pop(PROCESS_CONTEXT_ENV, None)
    return result


def capture_owner_unavailable(environ=None) -> str | None:
    """Consume only a fixed proxy-owned initial unavailability code, never raw paths."""
    source = os.environ if environ is None else environ
    code = source.pop(OWNER_UNAVAILABLE_ENV, None)
    if code is None:
        return None
    if code not in OWNER_UNAVAILABLE_REASONS:
        raise OwnerConnectionError("private owner availability code is invalid")
    return OWNER_UNAVAILABLE_REASONS[code]


class OwnerAttachment:
    """Own one inherited socket and at most one private client connection.

    Capture consumes the inherited descriptor. Copies of its public locator do
    not attach without that socket. Once captured, ordinary subprocesses inherit
    neither locator nor socket; only explicit Brain handoffs forward it.
    """

    def __init__(self, channel: socket.socket, transport: str):
        self._channel = channel
        self._transport = transport
        self._store: RemoteStateStore | None = None
        self._closed = False
        self._lock = RLock()
        channel.set_inheritable(False)

    @classmethod
    def capture(cls, environ=None) -> OwnerAttachment | None:
        """Consume a locator, distinguishing genuine absence from a broken channel."""
        source = os.environ if environ is None else environ
        raw = source.pop(OWNER_CHANNEL_ENV, None)
        if raw is None:
            return None
        match = _LOCATOR.fullmatch(raw) if isinstance(raw, str) else None
        if match is None:
            raise OwnerConnectionError("private owner descriptor locator is invalid")
        if os.name != "posix":
            raise OwnerTransportUnavailable("private process owner inheritance is unsupported on this platform")
        transport, value = match.groups()
        fd = int(value)
        if fd < 3:
            raise OwnerConnectionError("private owner cannot use a standard input/output descriptor")
        channel = None
        try:
            channel = socket.socket(fileno=fd)
            expected = socket.SOCK_STREAM if transport == "stream" else socket.SOCK_DGRAM
            if channel.family != socket.AF_UNIX or channel.type != expected:
                raise ValueError("owner descriptor has the wrong socket type")
            return cls(channel, transport)
        except (OSError, ValueError) as exc:
            if channel is not None:
                channel.close()
            raise OwnerConnectionError("private owner channel is unavailable; start or correctly forward a job context") from exc

    @classmethod
    def for_job(cls, owner: ConsentOwner) -> OwnerAttachment:
        """Own a duplicate rendezvous for deliberate root-program inheritance."""
        return cls(socket.socket(fileno=os.dup(owner.rendezvous_fd)), "job")

    @property
    def kind(self) -> str:
        return "mcp-instance" if self._transport == "stream" else "cli-job"

    def connect(self, vault_root: Path) -> RemoteStateStore:
        """Attach once and verify the selected canonical Brain before returning state."""
        with self._lock:
            self._require_open()
            root = str(Path(vault_root).resolve(strict=True))
            if self._store is None:
                try:
                    self._store = (
                        RemoteStateStore(socket.socket(fileno=os.dup(self._channel.fileno())))
                        if self._transport == "stream" else
                        RemoteStateStore.connect_inherited(self._channel.fileno())
                    )
                except Exception:
                    self.close()
                    raise
            if self._store.identity.vault_root != root:
                self.close()
                raise OwnerConnectionError("private owner belongs to a different Brain; start a fresh context")
            return self._store

    def forwarded_process(self, environ=None) -> dict:
        """Return Popen arguments for a trusted CLI descendant, never a shared stream."""
        with self._lock:
            self._require_open()
            if self._transport != "job":
                raise OwnerConnectionError("an MCP child stream cannot be forwarded to another process")
            return self._process_options(environ)

    def _process_options(self, environ=None) -> dict:
        if os.name != "posix":
            raise OwnerTransportUnavailable("private process owner inheritance is unsupported on this platform")
        env = without_owner_environment(environ)
        fd = self._channel.fileno()
        env[OWNER_CHANNEL_ENV] = f"{PROTOCOL}:{self._transport}:{fd}"
        return {"env": env, "pass_fds": (fd,), "close_fds": True}

    @contextmanager
    def exec_environment(self, environ=None):
        """Retain attachment only across exact process replacement, restoring on failure."""
        with self._lock:
            self._require_open()
            if self._transport == "stream" and self._store is not None:
                raise OwnerConnectionError("an established MCP stream cannot repeat its handshake after exec")
            options = self._process_options(environ)
            self._channel.set_inheritable(True)
            try:
                yield options["env"]
            finally:
                self._channel.set_inheritable(False)

    def close(self) -> None:
        """Close local handles without ending the owning proxy or job's context."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._store is not None:
                self._store.close()
            self._channel.close()

    def _require_open(self):
        if self._closed:
            raise OwnerConnectionError("private owner attachment has closed")


@contextmanager
def private_child_channel(owner: ConsentOwner, environ=None):
    """Serve a fresh MCP child stream and close the parent's child-end after spawn."""
    if os.name != "posix":
        raise OwnerTransportUnavailable("private MCP child inheritance is unsupported on this platform")
    child, server = socket.socketpair()
    try:
        owner.serve_connection(server)
        attachment = OwnerAttachment(child, "stream")
        yield attachment._process_options(environ)
    finally:
        child.close()
        # serve_connection takes server ownership, including on rejection.
        # On a failed spawn the child close supplies EOF to the owner thread.
