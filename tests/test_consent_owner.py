"""Opaque owner state preserves concurrency, lifetime and private process boundaries."""

import array
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import shutil
import struct
import subprocess
import sys
from threading import Barrier

import pytest

from _bootstrap.consent_owner import (
    ConsentOwner,
    MAX_FRAME_BYTES,
    OwnerConnectionError,
    OwnerTransportUnavailable,
    RemoteStateStore,
)
from _bootstrap.consent_state import (
    MemoryStateStore,
    StoreCapacityError,
    StoreClosedError,
    StoreConflictError,
)


SCRIPTS = Path(__file__).resolve().parents[1] / "src/brain-core/scripts"
HAS_FD_PASSING = hasattr(socket.socket, "sendmsg") and hasattr(socket, "SCM_RIGHTS")
POSIX_CHANNEL = pytest.mark.skipif(not HAS_FD_PASSING, reason="CLI descendant transport requires POSIX descriptor passing")


@pytest.fixture(params=["memory", "remote"])
def state(request, tmp_path):
    if request.param == "memory":
        store = MemoryStateStore()
        yield store
        store.close()
    else:
        owner = ConsentOwner(tmp_path)
        connection = owner.connect()
        yield connection
        connection.close()
        owner.close()


def test_snapshot_is_detached_and_reads_only_requested_keys(state):
    value = {"nested": ["one"]}
    assert state.compare_exchange({"a": 0, "b": 0}, {"a": value, "b": {"private": True}})
    value["nested"].append("outside")
    snapshot = state.snapshot(("a", "missing"))
    assert snapshot.values == {"a": {"nested": ["one"]}}
    assert snapshot.versions == {"a": 1, "missing": 0}
    snapshot.values["a"]["nested"].append("outside")
    assert state.snapshot(("a",)).values == {"a": {"nested": ["one"]}}


def test_atomic_comparison_checks_read_dependencies_and_never_partially_writes(state):
    assert state.compare_exchange({"read": 0, "write": 0}, {"read": 1, "write": 1})
    assert not state.compare_exchange({"read": 0, "write": 1}, {"write": 2})
    assert state.snapshot(("write",)).values == {"write": 1}
    with pytest.raises(ValueError, match="expected version"):
        state.compare_exchange({}, {"unguarded": 1})


def test_deletion_retains_versions_and_prevents_aba(state):
    assert state.compare_exchange({"a": 0}, {"a": 1})
    assert state.compare_exchange({"a": 1}, {"a": None})
    assert state.snapshot(("a",)).values == {}
    assert state.snapshot(("a",)).versions == {"a": 2}
    assert not state.compare_exchange({"a": 0}, {"a": "replayed"})
    assert state.compare_exchange({"a": 2}, {"a": "fresh"})
    assert state.snapshot(("a",)).versions == {"a": 3}


def test_paged_keys_are_live_filtered_and_revision_guarded(state):
    assert state.compare_exchange({"a:1": 0, "a:2": 0, "z": 0}, {"a:1": {}, "a:2": {}, "z": {}})
    first = state.list_keys(prefix="a:", limit=1)
    assert first.keys == ("a:1",)
    assert first.next_after == "a:1"
    second = state.list_keys(prefix="a:", limit=1, after=first.next_after, revision=first.revision)
    assert second.keys == ("a:2",)
    assert second.next_after is None
    assert state.compare_exchange({"a:2": 1}, {"a:2": None})
    with pytest.raises(StoreConflictError):
        state.list_keys(after=first.next_after, revision=first.revision)
    assert state.list_keys(prefix="a:").keys == ("a:1",)


def test_capacity_counts_tombstones_without_evicting_live_records():
    store = MemoryStateStore(max_bytes=100)
    assert store.compare_exchange({"retained": 0}, {"retained": "important"})
    index = 0
    with pytest.raises(StoreCapacityError):
        while True:
            key = f"deleted-{index}"
            store.compare_exchange({key: 0}, {key: None})
            index += 1
    assert index > 0
    assert store.snapshot(("retained",)).values == {"retained": "important"}
    assert store.snapshot((f"deleted-{index}",)).versions == {f"deleted-{index}": 0}


def test_capacity_failure_is_atomic_over_multiple_writes(tmp_path):
    owner = ConsentOwner(tmp_path, store=MemoryStateStore(max_bytes=100))
    remote = owner.connect()
    try:
        with pytest.raises(StoreCapacityError):
            remote.compare_exchange({"small": 0, "large": 0}, {"small": 1, "large": "x" * 100})
        assert remote.snapshot(("small", "large")).versions == {"small": 0, "large": 0}
    finally:
        remote.close()
        owner.close()


@pytest.mark.parametrize("value", [float("nan"), {1: "coercion"}, {"set": {1}}, ("tuple",)])
def test_non_json_state_is_rejected(value):
    with pytest.raises(ValueError):
        MemoryStateStore().compare_exchange({"key": 0}, {"key": value})


def test_close_permanently_rejects_reads_writes_and_scans():
    store = MemoryStateStore()
    store.close()
    store.close()
    for operation in (lambda: store.snapshot(()), lambda: store.compare_exchange({}, {}), lambda: store.list_keys()):
        with pytest.raises(StoreClosedError):
            operation()


def test_concurrent_compare_exchange_has_one_winner(tmp_path):
    owner = ConsentOwner(tmp_path)
    barrier = Barrier(12)
    def contender(index):
        connection = owner.connect()
        try:
            barrier.wait(timeout=5)
            return connection.compare_exchange({"grant": 0}, {"grant": {"invocation": index}})
        finally:
            connection.close()
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            assert sum(pool.map(contender, range(12))) == 1
        assert owner.store.snapshot(("grant",)).versions == {"grant": 1}
    finally:
        owner.close()


def test_connection_loss_does_not_close_other_clients_or_preserve_after_owner_end(tmp_path):
    owner = ConsentOwner(tmp_path)
    first = owner.connect()
    second = owner.connect()
    assert first.identity == second.identity
    assert first.compare_exchange({"value": 0}, {"value": 1})
    first.close()
    assert second.snapshot(("value",)).values == {"value": 1}
    owner.close()
    with pytest.raises(OwnerConnectionError):
        second.snapshot(("value",))
    fresh = ConsentOwner(tmp_path)
    try:
        assert fresh.identity.context_id != owner.identity.context_id
        assert fresh.store.snapshot(("value",)).values == {}
    finally:
        fresh.close()


def test_replaced_brain_root_ends_owner_context(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    owner = ConsentOwner(root)
    client = owner.connect()
    root.rename(tmp_path / "original")
    root.mkdir()
    try:
        with pytest.raises((OwnerConnectionError, StoreClosedError)):
            client.compare_exchange({"grant": 0}, {"grant": True})
        with pytest.raises(StoreClosedError):
            owner.store.snapshot(())
    finally:
        client.close()
        owner.close()


def test_root_identity_is_pinned_and_public_ids_cannot_select_another_owner(tmp_path):
    owner = ConsentOwner(tmp_path)
    client = owner.connect()
    try:
        assert client.identity.vault_root == str(tmp_path.resolve())
        with pytest.raises(ValueError, match="fields"):
            client._call({"operation": "snapshot", "keys": [], "context_id": "copied-public-id"})
        assert client.snapshot(()).values == {}
    finally:
        client.close()
        owner.close()


@pytest.mark.parametrize("payload", [struct.pack("!I", MAX_FRAME_BYTES + 1), struct.pack("!I", 1) + b"{", struct.pack("!I", 2) + b"[]"])
def test_bad_frames_close_only_the_bad_connection(tmp_path, payload):
    owner = ConsentOwner(tmp_path)
    client = owner.connect()
    try:
        client._channel.sendall(payload)
        assert client._channel.recv(1) == b""
        healthy = owner.connect()
        assert healthy.snapshot(()).values == {}
        healthy.close()
    finally:
        client.close()
        owner.close()


_CHILD = '''import sys
sys.path.insert(0,sys.argv[1])
from _bootstrap.consent_owner import RemoteStateStore
store=RemoteStateStore.connect_inherited(int(sys.argv[2]))
key=sys.argv[3]
for i in range(20):
    assert store.compare_exchange({key:i},{key:{"value":i,"owner":key}})
    assert store.snapshot((key,)).values[key]=={"value":i,"owner":key}
store.close()
print("ok")
'''


@POSIX_CHANNEL
def test_private_rendezvous_routes_concurrent_process_replies(tmp_path):
    owner = ConsentOwner(tmp_path)
    fd = owner.rendezvous_fd
    processes = []
    try:
        for index in range(8):
            processes.append(subprocess.Popen([sys.executable, "-c", _CHILD, str(SCRIPTS), str(fd), f"child-{index}"], pass_fds=(fd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            assert stdout.strip() == "ok"
        assert len(owner.store.list_keys().keys) == 8
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        owner.close()


@POSIX_CHANNEL
def test_shell_forwarding_and_descriptor_closing_are_explicit(tmp_path):
    owner = ConsentOwner(tmp_path)
    fd = owner.rendezvous_fd
    command = [sys.executable, "-c", _CHILD, str(SCRIPTS), str(fd), "shell"]
    try:
        shell = subprocess.run(["/bin/sh", "-c", 'exec "$@"', "sh", *command], pass_fds=(fd,), capture_output=True, text=True, timeout=10)
        assert shell.returncode == 0, shell.stderr
        missing = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert missing.returncode != 0
        assert "inherited owner channel is unavailable" in missing.stderr
    finally:
        owner.close()


@POSIX_CHANNEL
def test_malformed_rendezvous_does_not_poison_future_connections(tmp_path):
    owner = ConsentOwner(tmp_path)
    rendezvous = socket.socket(fileno=os.dup(owner.rendezvous_fd))
    try:
        rendezvous.send(b"invalid")
        rendezvous.send(b"x" * 100)
        connection = RemoteStateStore.connect_inherited(owner.rendezvous_fd)
        assert connection.snapshot(()).values == {}
        connection.close()
    finally:
        rendezvous.close()
        owner.close()


@POSIX_CHANNEL
def test_killing_real_owner_process_closes_established_connection(tmp_path):
    local, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    program = '''import array,socket,sys,time
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from _bootstrap.consent_owner import ConsentOwner
owner=ConsentOwner(Path(sys.argv[3]))
control=socket.socket(fileno=int(sys.argv[2]))
control.sendmsg([b"ready"],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array("i",[owner.rendezvous_fd]))])
while True: time.sleep(1)
'''
    process = subprocess.Popen([sys.executable, "-c", program, str(SCRIPTS), str(child.fileno()), str(tmp_path)], pass_fds=(child.fileno(),))
    child.close()
    received = None
    connection = None
    try:
        local.settimeout(5)
        data, ancillary, _flags, _address = local.recvmsg(64, socket.CMSG_SPACE(array.array("i").itemsize))
        assert data == b"ready"
        handles = array.array("i")
        handles.frombytes(ancillary[0][2])
        received = handles[0]
        connection = RemoteStateStore.connect_inherited(received)
        assert connection.compare_exchange({"grant": 0}, {"grant": True})
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(OwnerConnectionError):
            connection.snapshot(("grant",))
    finally:
        if connection is not None:
            connection.close()
        if received is not None:
            os.close(received)
        local.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        if connection is not None:
            shutil.rmtree(connection.identity.private_directory)


def test_missing_descriptor_passing_has_explicit_platform_remedy(tmp_path, monkeypatch):
    monkeypatch.delattr(socket, "SCM_RIGHTS", raising=False)
    owner = ConsentOwner(tmp_path)
    try:
        with pytest.raises(OwnerTransportUnavailable):
            _ = owner.rendezvous_fd
        connection = owner.connect()
        assert connection.snapshot(()).values == {}
        connection.close()
    finally:
        owner.close()


@pytest.mark.parametrize("keys", [("duplicate", "duplicate"), ({"unhashable": True},), (False,), ("x" * 257,)])
def test_invalid_snapshot_requests_are_rejected_without_poisoning_owner(state, keys):
    with pytest.raises(ValueError):
        state.snapshot(keys)
    assert state.snapshot(()).values == {}


def test_key_continuation_requires_revision(state):
    with pytest.raises(ValueError, match="revision"):
        state.list_keys(after="key")


@POSIX_CHANNEL
def test_plain_file_descriptor_does_not_establish_owner_attachment(tmp_path):
    file = tmp_path / "not-a-channel"
    file.write_text("public-context-id")
    with file.open() as stream:
        with pytest.raises(OwnerConnectionError):
            RemoteStateStore.connect_inherited(stream.fileno())
        assert not stream.closed
        assert stream.read() == "public-context-id"


def test_private_directory_belongs_to_owner_and_survives_client_replacement(tmp_path):
    owner = ConsentOwner(tmp_path)
    first = owner.connect()
    private = owner.private_directory
    try:
        assert private.is_dir()
        if os.name == "posix":
            assert private.stat().st_mode & 0o777 == 0o700
        assert first.identity.private_directory == str(private)
        (private / "pin").write_text("immutable prepared content")
        first.close()
        second = owner.connect()
        assert second.identity.private_directory == str(private)
        assert (private / "pin").read_text() == "immutable prepared content"
        second.close()
    finally:
        owner.close()
    assert not private.exists()


def test_closed_owner_pin_use_cannot_recreate_private_directory(tmp_path):
    from _command_interface.consent_staging import ConsentContentPins

    owner = ConsentOwner(tmp_path)
    pins = ConsentContentPins(owner.private_directory, owner.store, namespace="operation",
                              coordination_path=owner.coordination_path)
    owner.close()
    with pytest.raises((StoreClosedError, FileNotFoundError)):
        pins.retain_content("source", b"private")
    assert not owner.private_directory.exists()


@pytest.mark.parametrize("pause_at", ["snapshot", "write"])
def test_close_waits_for_pin_user_without_blocking_owner_shutdown(tmp_path, monkeypatch, pause_at):
    from threading import Event
    from _command_interface import consent_staging

    started, resume, closed = Event(), Event(), Event()

    class ClosingStore(MemoryStateStore):
        def close(self):
            super().close()
            closed.set()

    store = ClosingStore()
    owner = ConsentOwner(tmp_path, store=store)
    pins = consent_staging.ConsentContentPins(owner.private_directory, store, namespace="operation",
                              coordination_path=owner.coordination_path)
    original = store.snapshot if pause_at == "snapshot" else consent_staging.safe_write_via

    def paused(*args, **kwargs):
        if not started.is_set():
            started.set()
            assert resume.wait(5), "test did not release the pin user"
        return original(*args, **kwargs)

    monkeypatch.setattr(store if pause_at == "snapshot" else consent_staging,
                        "snapshot" if pause_at == "snapshot" else "safe_write_via", paused)
    with ThreadPoolExecutor(max_workers=2) as workers:
        retain = workers.submit(pins.retain_content, "source", b"private")
        try:
            assert started.wait(5)
            ending = workers.submit(owner.close)
            assert closed.wait(5), "owner shutdown blocked behind pin user"
            assert not ending.done()
            assert owner.private_directory.is_dir()
        finally:
            resume.set()
        with pytest.raises(StoreClosedError):
            retain.result(timeout=5)
        ending.result(timeout=5)
    assert not owner.private_directory.exists()


def test_coordination_is_shared_per_brain_outside_private_content(tmp_path):
    first, second = ConsentOwner(tmp_path), ConsentOwner(tmp_path)
    try:
        assert first.coordination_path == second.coordination_path
        assert not first.coordination_path.is_relative_to(first.private_directory)
        remote = first.connect()
        try:
            assert remote.identity.coordination_path == str(first.coordination_path)
        finally:
            remote.close()
        (first.private_directory / "body").write_bytes(b"private")
        first.close()
        assert not first.private_directory.exists()
        assert first.coordination_path.is_file()
        assert second.store.snapshot(()).values == {}
    finally:
        first.close()
        second.close()


def test_close_waits_for_materialising_pin_read(tmp_path, monkeypatch):
    from threading import Event
    from _command_interface.consent_staging import ConsentContentPins

    reading, resume, closed = Event(), Event(), Event()

    class ClosingStore(MemoryStateStore):
        def close(self):
            super().close()
            closed.set()

    owner = ConsentOwner(tmp_path, store=ClosingStore())
    pins = ConsentContentPins(owner.private_directory, owner.store, namespace="operation",
                              coordination_path=owner.coordination_path)
    pins.retain_content("source", b"materialised")
    original = pins._read

    def paused(record):
        reading.set()
        assert resume.wait(5)
        return original(record)

    monkeypatch.setattr(pins, "_read", paused)
    with ThreadPoolExecutor(max_workers=2) as workers:
        read = workers.submit(pins.read_pinned, "source")
        try:
            assert reading.wait(5)
            ending = workers.submit(owner.close)
            assert closed.wait(5)
            assert not ending.done()
        finally:
            resume.set()
        assert read.result(timeout=5) == b"materialised"
        ending.result(timeout=5)
    assert not owner.private_directory.exists()
    with pytest.raises(StoreClosedError):
        pins.read_pinned("source")
