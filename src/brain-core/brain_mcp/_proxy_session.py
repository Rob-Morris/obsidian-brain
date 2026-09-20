"""SDK-independent public session and host-owned subscription state."""

from copy import deepcopy
from dataclasses import dataclass
import json
import itertools
import threading


LEGACY_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MODERN_VERSION = "2026-07-28"
SUBSCRIPTION_ID = "io.modelcontextprotocol/subscriptionId"
EVENT_FILTERS = {
    "notifications/tools/list_changed": "toolsListChanged",
    "notifications/prompts/list_changed": "promptsListChanged",
    "notifications/resources/list_changed": "resourcesListChanged",
    "notifications/resources/updated": "resourceSubscriptions",
}
MAX_SUBSCRIPTIONS = 128
MAX_SUBSCRIPTION_BYTES = 64 * 1024
MAX_PENDING_EVENTS = 1024


@dataclass(frozen=True)
class SubscriptionEvent:
    key: tuple
    frame: dict


def capabilities(*, modern: bool) -> dict:
    """Brain's public transport obligations, checked against the real SDK."""
    return {"prompts": {"listChanged": modern},
            "resources": {"listChanged": modern, "subscribe": modern},
            "tools": {"listChanged": True}}


def discovery(request: dict) -> dict:
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    modern = request.get("method") != "initialize"
    if modern:
        metadata = params.get("_meta", {})
        if not isinstance(metadata, dict):
            raise ValueError("protocol metadata must be an object")
        version = metadata.get("io.modelcontextprotocol/protocolVersion")
        if version != MODERN_VERSION:
            raise ValueError("unsupported MCP protocol version")
        result = {"supportedVersions": [MODERN_VERSION]}
    else:
        offered = params.get("protocolVersion")
        result = {"protocolVersion": offered if offered in LEGACY_VERSIONS else LEGACY_VERSIONS[-1]}
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": {
        **result, "serverInfo": {"name": "brain", "version": ""},
        "capabilities": capabilities(modern=modern)}}


def _filter(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("subscription notifications must be an object")
    result = {}
    for key in EVENT_FILTERS.values():
        item = value.get(key)
        if key == "resourceSubscriptions":
            if item is not None and (not isinstance(item, list) or
                                     any(not isinstance(uri, str) for uri in item)):
                raise ValueError("resource subscriptions must be URI strings")
            if item:
                result[key] = list(dict.fromkeys(item))
        else:
            if item is not None and type(item) is not bool:
                raise ValueError("subscription flags must be booleans")
            if item:
                result[key] = True
    return result


class Subscriptions:
    """Standing host streams are transport state, never admitted child work."""

    def __init__(self):
        self._lock = threading.RLock()
        self._streams = {}
        self._pending = set()
        self._tokens = {}
        self._sequence = itertools.count()

    def open(self, request_id, filters, publish):
        if type(request_id) not in (str, int):
            raise ValueError("subscription requires a string or integer request ID")
        filters = _filter(filters)
        with self._lock:
            if request_id in self._streams:
                raise ValueError("subscription request ID already active")
            candidate = [*self.snapshot(), {"id": request_id, "notifications": filters}]
            if len(candidate) > MAX_SUBSCRIPTIONS or len(json.dumps(candidate).encode()) > MAX_SUBSCRIPTION_BYTES:
                raise ValueError("subscription limit reached")
            self._streams[request_id] = filters
            self._tokens[request_id] = next(self._sequence)
            publish({"jsonrpc": "2.0", "method": "notifications/subscriptions/acknowledged",
                     "params": {"notifications": deepcopy(filters), "_meta": {SUBSCRIPTION_ID: request_id}}})

    def contains(self, request_id):
        with self._lock:
            return request_id in self._streams

    def cancel(self, request_id):
        with self._lock:
            self._tokens.pop(request_id, None)
            return self._streams.pop(request_id, None) is not None

    def clear(self):
        with self._lock:
            self._streams.clear()
            self._pending.clear()
            self._tokens.clear()

    def end(self, reason, publish):
        with self._lock:
            for request_id in tuple(self._streams):
                self.cancel(request_id)
                publish({"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": request_id, "reason": reason}})

    def current(self, key):
        with self._lock:
            return self._tokens.get(key[0]) == key[1]

    def delivered(self, key):
        with self._lock:
            self._pending.discard(key)

    def emit(self, event, publish):
        method = event.get("method")
        key = EVENT_FILTERS.get(method)
        if key is None:
            return
        params = event.get("params", {})
        with self._lock:
            for request_id, filters in tuple(self._streams.items()):
                matches = params.get("uri") in filters.get(key, []) if key == "resourceSubscriptions" else filters.get(key) is True
                if matches:
                    pending = (request_id, self._tokens[request_id], method, params.get("uri"))
                    if pending in self._pending:
                        continue
                    if len(self._pending) >= MAX_PENDING_EVENTS:
                        self.cancel(request_id)
                        publish({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                 "params": {"requestId": request_id, "reason": "subscription backlog full; re-listen and refetch"}})
                        continue
                    self._pending.add(pending)
                    publish(SubscriptionEvent(pending, {"jsonrpc": "2.0", "method": method,
                             "params": {**params, "_meta": {SUBSCRIPTION_ID: request_id}}}))

    def snapshot(self):
        with self._lock:
            return [{"id": key, "notifications": deepcopy(value)} for key, value in self._streams.items()]

    def invalidate(self, publish):
        """Refetch after confirmed upstream coverage closes any resync gap."""
        with self._lock:
            streams = self.snapshot()
            for method, key in EVENT_FILTERS.items():
                if key == "resourceSubscriptions":
                    for uri in dict.fromkeys(uri for stream in streams for uri in stream["notifications"].get(key, [])):
                        self.emit({"method": method, "params": {"uri": uri}}, publish)
                elif any(stream["notifications"].get(key) for stream in streams):
                    self.emit({"method": method}, publish)

    @classmethod
    def restore(cls, values):
        if not isinstance(values, list):
            raise ValueError("invalid subscription state")
        result = cls()
        for item in values:
            if not isinstance(item, dict) or set(item) != {"id", "notifications"}:
                raise ValueError("invalid subscription fields")
            result.open(item["id"], item["notifications"], lambda _: None)
        return result
