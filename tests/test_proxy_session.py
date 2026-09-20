"""The host session survives application absence, not merely an open pipe."""

import pytest

from brain_mcp import _proxy_session as session
from brain_mcp._proxy_handoff import public_session


def test_host_stream_filters_order_limits_and_cancellation(monkeypatch):
    streams = session.Subscriptions()
    frames = []
    streams.open(1, {"toolsListChanged": True, "resourceSubscriptions": ["brain://one"]}, frames.append)
    streams.open("prompts", {"promptsListChanged": True, "unknownExtension": True}, frames.append)
    streams.emit({"method": "notifications/tools/list_changed"}, frames.append)
    streams.emit({"method": "notifications/resources/updated", "params": {"uri": "brain://other"}}, frames.append)
    streams.emit({"method": "notifications/resources/updated", "params": {"uri": "brain://one"}}, frames.append)
    frames = [frame.frame if isinstance(frame, session.SubscriptionEvent) else frame for frame in frames]
    assert [frame["method"] for frame in frames] == [
        "notifications/subscriptions/acknowledged", "notifications/subscriptions/acknowledged",
        "notifications/tools/list_changed", "notifications/resources/updated"]
    assert all(frame["params"]["_meta"][session.SUBSCRIPTION_ID] == 1 for frame in frames[2:])
    assert "unknownExtension" not in frames[1]["params"]["notifications"]
    assert streams.cancel(1)
    streams.emit({"method": "notifications/tools/list_changed"}, frames.append)
    assert len(frames) == 4
    monkeypatch.setattr(session, "MAX_SUBSCRIPTIONS", 1)
    with pytest.raises(ValueError, match="limit"):
        streams.open(2, {}, frames.append)


@pytest.mark.parametrize("filters", [None, [], {"toolsListChanged": 1}, {"resourceSubscriptions": [3]}])
def test_invalid_subscription_is_not_acknowledged(filters):
    frames = []
    with pytest.raises(ValueError):
        session.Subscriptions().open(1, filters, frames.append)
    assert not frames


def test_handoff_state_is_bounded_independent_and_validated():
    streams = session.Subscriptions()
    streams.open("a", {"resourcesListChanged": True}, lambda _: None)
    restored = session.Subscriptions.restore(streams.snapshot())
    streams.clear()
    assert restored.contains("a")
    with pytest.raises(ValueError, match="already active"):
        session.Subscriptions.restore(restored.snapshot() * 2)
    with pytest.raises(ValueError, match="limit"):
        restored.open("large", {"resourceSubscriptions": ["a" * session.MAX_SUBSCRIPTION_BYTES]}, lambda _: None)


def test_slow_writer_coalesces_events_and_ends_overfull_stream(monkeypatch):
    monkeypatch.setattr(session, "MAX_PENDING_EVENTS", 1)
    streams, frames = session.Subscriptions(), []
    streams.open(1, {"toolsListChanged": True, "promptsListChanged": True}, frames.append)
    for _ in range(100):
        streams.emit({"method": "notifications/tools/list_changed"}, frames.append)
    assert len(frames) == 2
    streams.emit({"method": "notifications/prompts/list_changed"}, frames.append)
    assert frames[-1]["method"] == "notifications/cancelled"
    assert not streams.contains(1)
    streams.delivered(frames[1].key)


def test_public_policy_matches_actual_sdk_handlers():
    from mcp.server import MCPServer
    from brain_mcp._proxy_controls import add_control_discovery
    server = MCPServer(name="brain")
    for modern in (False, True):
        actual = server._lowlevel_server.get_capabilities(
            protocol_version=session.MODERN_VERSION if modern else session.LEGACY_VERSIONS[-1])
        wire = actual.model_dump(by_alias=True, exclude_none=True)
        if not modern:
            wire = add_control_discovery({"result": {"capabilities": wire}},
                                         {"method": "initialize"})["result"]["capabilities"]
        assert wire == session.capabilities(modern=modern)


def test_discovery_never_fabricates_an_application_header():
    request = {"id": 1, "method": "server/discover", "params": {"_meta": {
        "io.modelcontextprotocol/protocolVersion": session.MODERN_VERSION}}}
    result = session.discovery(request)
    assert "experimental" not in result["result"]["capabilities"]
    assert public_session(result)["supportedVersions"] == [session.MODERN_VERSION]
    request["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"
    with pytest.raises(ValueError, match="unsupported"):
        session.discovery(request)
