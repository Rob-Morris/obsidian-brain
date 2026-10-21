"""Private kernel handoff input: no secret bytes in the state file."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_handoff import (
    HANDOFF_VERSION, MAX_STATE_BYTES, READ_CHUNK, RawLineReader,
    consume_remainder, read_state, state_descriptor,
)

SECRET = b"synthetic-private-handoff-credential"
CORE = Path(__file__).resolve().parents[1] / "src/brain-core"


def state(data=b""):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(CORE.parent))
    relay._client_protocol = "modern"
    relay._public_session = {}
    return relay._transport_state("restart", RawLineReader(0, data))


@contextmanager
def wire_descriptor(value):
    """Untrusted/legacy descriptor fixture, independent of the v3 emitter."""
    with tempfile.TemporaryFile() as stream:
        stream.write(json.dumps(value).encode())
        stream.flush()
        yield stream.fileno()


@pytest.mark.parametrize("data", [b"", SECRET, SECRET + b"x" * (READ_CHUNK - len(SECRET))])
def test_state_file_contains_only_input_descriptor_and_preflight_never_consumes(data):
    with state_descriptor(state(data)) as fd:
        raw = os.pread(fd, MAX_STATE_BYTES + 1, 0)
        assert SECRET not in raw and base64.b64encode(SECRET) not in raw
        if data:
            assert base64.b64encode(data) not in raw
        wire = json.loads(raw)
        assert wire["version"] == 3
        assert set(wire["remainder"]) == {"fd", "bytes"}
        assert wire["remainder"]["bytes"] == len(data)
        assert read_state(fd, expected_pid=os.getpid()) == wire
        assert read_state(fd, expected_pid=os.getpid()) == wire
        # Own a duplicate as the replacement process would. Consumption closes it.
        owned = {**wire, "remainder": {**wire["remainder"], "fd": os.dup(wire["remainder"]["fd"])}}
        assert consume_remainder(owned) == data
        with pytest.raises(ValueError, match="input descriptor"):
            consume_remainder(owned)
    with pytest.raises(OSError):
        os.fstat(wire["remainder"]["fd"])


@pytest.mark.parametrize("channel", [None, {}, {"fd": 0, "bytes": 0}, {"fd": True, "bytes": 0},
                                      {"fd": 1000000, "bytes": 0}, {"fd": 3, "bytes": -1},
                                      {"fd": 3, "bytes": READ_CHUNK + 1}, {"fd": 3, "bytes": False},
                                      {"fd": 3, "bytes": 0, "credential": SECRET.decode()}])
def test_malformed_input_descriptors_fail_closed_without_echo(channel):
    with wire_descriptor({**state(), "remainder": channel}) as fd:
        with pytest.raises(ValueError) as caught:
            read_state(fd, expected_pid=os.getpid())
        assert SECRET.decode() not in str(caught.value)


def test_regular_file_cannot_impersonate_kernel_input():
    with tempfile.TemporaryFile() as payload:
        payload.write(SECRET)
        payload.flush()
        with wire_descriptor({**state(), "remainder": {"fd": payload.fileno(), "bytes": len(SECRET)}}) as fd:
            with pytest.raises(ValueError, match="input descriptor"):
                read_state(fd, expected_pid=os.getpid())


def test_channel_count_and_socket_type_are_checked():
    with state_descriptor(state(SECRET)) as fd:
        wire = json.loads(os.pread(fd, MAX_STATE_BYTES, 0))
        invalid = {**wire, "remainder": {**wire["remainder"], "bytes": len(SECRET) - 1}}
        with wire_descriptor(invalid) as wrong:
            with pytest.raises(ValueError, match="byte count"):
                read_state(wrong, expected_pid=os.getpid())
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        with wire_descriptor({**state(), "remainder": {"fd": left.fileno(), "bytes": 0}}) as fd:
            with pytest.raises(ValueError, match="input descriptor"):
                read_state(fd, expected_pid=os.getpid())
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("version", [1, 2])
def test_installed_legacy_handoff_remains_readable(version):
    value = {**state(), "version": version, "remainder": base64.b64encode(SECRET).decode()}
    if version == 1:
        del value["subscriptions"]
        del value["resolution"]
    with wire_descriptor(value) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
        assert consume_remainder(restored) == SECRET
    # Current emission must not silently reproduce the old secret-bearing format.
    with pytest.raises(ValueError, match="v3"):
        with state_descriptor(value):
            pytest.fail("legacy state was emitted")


@pytest.mark.skipif(os.name != "posix", reason="inherited Unix sockets and exec")
def test_actual_preflight_then_exec_preserves_pipelined_and_split_credential_requests():
    first = {"id": 10, "method": "tools/call", "params": {"name": "command_list", "arguments": {"brain_session": SECRET.decode()}}}
    second = {"id": 11, "method": "tools/call", "params": {"name": "command_describe", "arguments": {"brain_session": SECRET.decode()}}}
    encoded = json.dumps(second).encode() + b"\n"
    cut = encoded.index(SECRET) + 9  # Split inside the credential itself.
    buffered = json.dumps(first).encode() + b"\n" + encoded[:cut]
    env = {**os.environ, "PYTHONPATH": str(CORE), "PYTHONDONTWRITEBYTECODE": "1"}
    preflight = """
import os,sys
from brain_mcp._proxy_handoff import read_state
read_state(int(sys.argv[1]),expected_pid=os.getppid())
"""
    consumer = """
import json,os,sys
from brain_mcp._proxy_handoff import RawLineReader,consume_remainder,read_state
state=read_state(int(sys.argv[1]),expected_pid=int(sys.argv[2]))
os.close(int(sys.argv[1]))
reader=RawLineReader(0,consume_remainder(state))
for unused in range(2):
    request=json.loads(reader.readline())
    # Echo only in this test's caller output; no fixture/state file receives it.
    print(json.dumps({'id':request['id'],'result':request['params']['arguments']}),flush=True)
assert reader.readline()==b''
"""
    trampoline = "import os,sys; os.execve(sys.executable,[sys.executable,'-c',sys.argv[1],*sys.argv[2:]],os.environ)"
    with state_descriptor(state(buffered)) as fd:
        wire = read_state(fd, expected_pid=os.getpid())
        descriptors = (fd, wire["remainder"]["fd"])
        subprocess.run([sys.executable, "-c", preflight, str(fd)], env=env, pass_fds=descriptors,
                       check=True, capture_output=True, timeout=10)
        assert read_state(fd, expected_pid=os.getpid()) == wire
        actual = subprocess.run([sys.executable, "-c", trampoline, consumer, str(fd), str(os.getpid())],
                                env=env, pass_fds=descriptors, input=encoded[cut:],
                                capture_output=True, check=True, timeout=10)
        responses = [json.loads(line) for line in actual.stdout.splitlines()]
        assert responses == [{"id": request["id"], "result": request["params"]["arguments"]}
                             for request in (first, second)]
        raw = os.pread(fd, MAX_STATE_BYTES + 1, 0)
        assert SECRET not in raw and base64.b64encode(SECRET) not in raw


def test_actual_handoff_entry_consumes_and_closes_input_before_serving(tmp_path, monkeypatch):
    from types import SimpleNamespace

    value = {**state(SECRET), "vault": str(tmp_path)}
    monkeypatch.setattr(proxy, "_logger", None)
    for name in ("BRAIN_VAULT_ROOT", "PYTHONPATH", "BRAIN_WORKSPACE_DIR"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setattr(proxy, "__file__", str(tmp_path / ".brain-core/brain_mcp/proxy.py"))
    monkeypatch.setattr(proxy, "same_executable_path", lambda *args: True)
    monkeypatch.setattr(proxy, "_setup_logging", lambda *args: SimpleNamespace())
    monkeypatch.setattr(proxy._operational_log, "install", lambda *args: None)
    seen = []
    monkeypatch.setattr(proxy, "_serve_proxy", lambda *args, **kwargs: seen.append(kwargs["state"]))
    with state_descriptor(value) as fd:
        wire = read_state(fd, expected_pid=os.getpid())
        owned_input = os.dup(wire["remainder"]["fd"])
        owned_state = {**wire, "remainder": {**wire["remainder"], "fd": owned_input}}
        with wire_descriptor(owned_state) as fixture_fd:
            entry_fd = os.dup(fixture_fd)
            proxy._handoff_entry("--handoff-fd", entry_fd)
            with pytest.raises(OSError):
                os.fstat(entry_fd)
            with pytest.raises(OSError):
                os.fstat(owned_input)
    assert len(seen) == 1
    assert seen[0]["remainder"] == SECRET


def test_failed_preflight_scope_closes_channel_and_keeps_original_input():
    original = state(SECRET)
    with pytest.raises(RuntimeError, match="preflight refused"):
        with state_descriptor(original) as fd:
            wire = read_state(fd, expected_pid=os.getpid())
            input_fd = wire["remainder"]["fd"]
            raise RuntimeError("preflight refused")
    assert original["remainder"] == SECRET
    with pytest.raises(OSError):
        os.fstat(input_fd)


def test_insufficient_channel_capacity_fails_before_any_state_file(monkeypatch):
    import brain_mcp._proxy_handoff as handoff

    real_pair = handoff.socket.socketpair
    created = []

    class LimitedWriter:
        def __init__(self, stream):
            self.stream = stream
        def setsockopt(self, *args):
            pass
        def getsockopt(self, *args):
            return 1
        def close(self):
            self.stream.close()

    def limited_pair(*args):
        reader, writer = real_pair(*args)
        created.extend((reader.fileno(), writer.fileno()))
        return reader, LimitedWriter(writer)

    monkeypatch.setattr(handoff.socket, "socketpair", limited_pair)
    monkeypatch.setattr(handoff.tempfile, "TemporaryFile", lambda: pytest.fail("state file opened before channel admission"))
    with pytest.raises(ValueError, match="capacity unavailable"):
        with state_descriptor(state(SECRET)):
            pytest.fail("undersized input channel admitted")
    for fd in created:
        with pytest.raises(OSError):
            os.fstat(fd)
