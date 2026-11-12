"""Correlate bounded child probes without imposing an operation deadline."""
from dataclasses import dataclass
import threading
import uuid

from ._interface_protocol import interface_header_from_response


@dataclass(frozen=True)
class HealthAction:
    kind: str
    request_id: str
    method: str = "ping"


class ChildHealth:
    """Only a current child's matching ping reply renews its health lease."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._child = None
        self._prefix = ""
        self._sequence = 0
        self._pending = None
        self._next = 0.0

    def poll(self, child, busy: bool, now: float, *, modern: bool = False) -> HealthAction | None:
        with self._lock:
            if child is not self._child:
                self._child = child
                self._prefix = "brain-proxy-health-" + uuid.uuid4().hex + "-"
                self._sequence = 0
                self._pending = None
                self._next = now + self.interval
            if child is None or not busy:
                self._pending = None
                self._next = now + self.interval
                return None
            if self._pending is not None:
                request_id, deadline, _modern = self._pending
                if now >= deadline:
                    self._pending = None
                    self._next = now + self.interval
                    return HealthAction("stop", request_id)
                return None
            if now >= self._next:
                self._sequence += 1
                request_id = self._prefix + str(self._sequence)
                self._pending = (request_id, now + 2 * self.interval, modern)
                return HealthAction("probe", request_id, "server/discover" if modern else "ping")
            return None

    def consume(self, child, message: dict, now: float) -> bool:
        """Hide owned probe replies; malformed, duplicate or stale replies do not renew."""
        with self._lock:
            request_id = message.get("id")
            if child is not self._child or not isinstance(request_id, str):
                return False
            if not request_id.startswith(self._prefix):
                return False
            suffix = request_id[len(self._prefix):]
            if (not suffix.isascii() or not suffix.isdecimal()
                    or len(suffix) > len(str(self._sequence))
                    or not 1 <= int(suffix) <= self._sequence):
                return True
            result = message.get("result")
            empty_result = (isinstance(result, dict) and not set(result) - {"_meta"}
                and ("_meta" not in result or isinstance(result["_meta"], dict)))
            if self._pending is not None and self._pending[2]:
                try:
                    interface_header_from_response(message)
                    empty_result = result.get("resultType") == "complete"
                except ValueError:
                    empty_result = False
            if (self._pending is not None and request_id == self._pending[0]
                    and now < self._pending[1] and empty_result
                    and message.get("jsonrpc") == "2.0"
                    and "error" not in message and "method" not in message):
                self._pending = None
                self._next = now + self.interval
            return True
