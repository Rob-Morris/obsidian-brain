#!/usr/bin/env python3
"""
Brain MCP Proxy — thin stdio proxy between MCP client and brain MCP server.

Owns the stdio channel so it survives server restarts. Spawns the configured
server target as a child subprocess, forwards messages bidirectionally, and
handles restart logic with exponential backoff.

Usage:
    python -m brain_mcp.proxy <python_path> <server_target>

Env:
    BRAIN_VAULT_ROOT        — vault path (passed through to child)
    BRAIN_WORKSPACE_DIR     — optional active workspace path (passed through to child)
    BRAIN_LOG_BODIES        — when "1" or "true", capture raw JSON bodies of every
                              forwarded message to .brain/local/diagnostics/debug-bodies.log
    BRAIN_PROXY_BACKOFF     — comma-separated int seconds (default: 0,4,8,16,32)
    BRAIN_PROXY_INIT_TIMEOUT — seconds to wait for child initialize response (default 60)
    BRAIN_PROXY_VERSION_CHECK_INTERVAL — rate-limit for version-reset checks (default 5)
    BRAIN_MCP_PROXY_PROTOCOL — set by this running proxy for the child handshake
"""

from contextlib import nullcontext
from datetime import datetime, timezone
from dataclasses import asdict, dataclass
import base64
import hashlib
import itertools
import json
import logging
import os
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from _bootstrap.runtime import same_executable_path
from _bootstrap.consent_owner import ConsentOwner
from _bootstrap.file_lock import MutationLockError
from _bootstrap.owner_attachment import (
    OWNER_UNAVAILABLE_ENV, OWNER_UNAVAILABLE_REASONS,
    PROCESS_CONTEXT_ENV, ProcessIdentity,
    private_child_channel, without_owner_environment,
)
from _bootstrap.workspace_binding import (
    WORKSPACE_ERROR_FILESYSTEM_ACCESS,
    WorkspaceBindingError,
    resolve_brain_target,
)
from _common import find_existing_central_venv
from _common import _operational_log
from _repair_common import build_repair_command
from ._interface_protocol import (
    PROXY_PROTOCOL,
    PROXY_PROTOCOL_ENV,
    AcceptedCallRecord,
    CommandInterfaceHeader,
    InterfaceTool,
    accept_call,
    interface_header_from_response,
)
from ._result_content import result_text_wire
from ._proxy_controls import (
    CONTROL_TOOLS, REFRESH_TOOL, RESTART_TOOL, STATUS_TOOL,
    add_control_discovery, control_response, tool_definitions,
)

from ._proxy_handoff import (
    HANDOFF_TIMEOUT, HANDOFF_VERSION, READ_CHUNK, RawLineReader, public_session,
    read_state, state_descriptor,
)
from ._proxy_session import (
    Subscriptions, SubscriptionEvent, SUBSCRIPTION_ID, EVENT_FILTERS, discovery,
)


@dataclass(frozen=True)
class StartupFailure:
    phase: str
    code: str
    detail: str

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROXY_VERSION = "0.10.5"
_CHILD_PROTOCOL_VERSION = "2026-07-28"


def _installed_runtime_python(vault_root: str | Path) -> str:
    selected = find_existing_central_venv(Path(vault_root))
    if selected is None:
        raise FileNotFoundError("No installed managed runtime matches this Brain's dependencies.")
    return str(selected)


def _internal_request(method: str, request_id: str, params: dict, *, modern: bool) -> dict:
    """Make a proxy-owned request in the child's negotiated protocol era."""

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            **params,
            "_meta": ({
                "io.modelcontextprotocol/protocolVersion": _CHILD_PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientInfo": {
                    "name": "brain-proxy", "version": PROXY_VERSION,
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            } if modern else {}),
        },
    }


def _uses_modern_protocol(request: dict) -> bool:
    params = request.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    return (request.get("method") != "initialize" and isinstance(metadata, dict)
            and "io.modelcontextprotocol/protocolVersion" in metadata)

_DEFAULT_BACKOFF = [0, 4, 8, 16, 32]
_CHILD_ALIVE_RESET_SECS = 60  # reset backoff if child lives this long
_EXIT_CODE_VERSION_DRIFT = 10
_EXIT_CODE_CLEAN = 0
_READER_SELECT_TIMEOUT = 30  # seconds; override with BRAIN_PROXY_READ_TIMEOUT
_HANG_CONSECUTIVE_LIMIT = 3  # kill child after this many timeouts with in-flight requests
_MAX_REPLAY_DEPTH = 1  # cap replay to prevent infinite drift loops
_VERSION_CHECK_INTERVAL = 5  # seconds; override with BRAIN_PROXY_VERSION_CHECK_INTERVAL
_GUIDANCE_BINDING = "Fix the binding or machine default, then restart MCP."
_GUIDANCE_FILESYSTEM = "Fix the filesystem permissions or mount state, then restart MCP."
_GUIDANCE_VAULT_FILESYSTEM = "Fix the vault filesystem permissions or mount state, then restart MCP."
_GUIDANCE_BY_BINDING_CODE = {
    WORKSPACE_ERROR_FILESYSTEM_ACCESS: _GUIDANCE_FILESYSTEM,
}


def _unrecoverable_msg(reason: str) -> str:
    """Build a user-facing error message for an unrecoverable proxy state.

    All such messages share the `MCP unrecoverable — ...` prefix so clients
    can recognise terminal states regardless of cause.
    """
    return f"MCP unrecoverable — {reason}. Restart MCP to recover."


_RECOVERY_THREAD_CRASHED_MSG = _unrecoverable_msg("proxy recovery thread crashed")


def _read_child_line_with_timeout(child: "ChildProcess", timeout: int) -> bytes | None:
    """Read one child stdout line with a timeout.

    Unix can wait on pipe fds with select.  Windows select only supports
    sockets, so pipe reads run in a daemon helper thread and the caller waits
    on the queue.  A timed-out Windows read may leave that daemon blocked until
    the child pipe closes; this is acceptable for the initialize handshake and
    avoids wedging the proxy on a healthy child.
    """
    if sys.platform == "win32":
        result: queue.Queue[bytes | None] = queue.Queue(maxsize=1)

        def read_once() -> None:
            try:
                result.put(child.readline())
            except OSError:
                result.put(None)

        reader = threading.Thread(target=read_once, daemon=True, name="child-line-timeout")
        reader.start()
        try:
            return result.get(timeout=timeout)
        except queue.Empty:
            return None

    fd = child.stdout_fd
    if fd is None:
        return None
    try:
        ready = [fd] if child.line_ready else select.select([fd], [], [], timeout)[0]
    except (ValueError, OSError):
        return None
    if not ready:
        return None
    return child.readline()

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

_logger: logging.Logger | None = None


def _setup_logging(vault_root: str) -> logging.Logger:
    """Configure human-readable stderr logging for the proxy.

    Persistent diagnostics use the structured operational log instead
    (`_common._operational_log`); the prose logger keeps only its
    WARNING-and-above stderr surface.
    """
    del vault_root
    logger = logging.getLogger("brain-proxy")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("[brain-proxy] %(levelname)s: %(message)s"))
    logger.addHandler(stderr_handler)

    return logger


def _probe_local_state(vault_root: str) -> None:
    """Prove `.brain/local` is writable before serving.

    The old file-logging setup doubled as this health gate; the gate outlives
    it because an unwritable local state directory breaks receipts, access
    state and runtime status — not just diagnostics.
    """
    local_dir = os.path.join(vault_root, ".brain", "local")
    os.makedirs(local_dir, exist_ok=True)
    probe = os.path.join(
        local_dir,
        f".writable-probe-{os.getpid()}-{uuid.uuid4().hex}",
    )
    descriptor = -1
    created = False
    try:
        descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            try:
                os.unlink(probe)
            except FileNotFoundError:
                pass


def _op_event(event: str, *, family: str | None = None, **fields) -> None:
    """Emit one operational record when the proxy logger is installed."""
    logger = _operational_log.current_logger()
    if logger is not None:
        logger.record(event, family=family, **fields)


def _capture_body(direction: str, method: object, raw: bytes) -> None:
    """Capture one raw wire body to the opt-in debug-bodies family."""
    logger = _operational_log.current_logger()
    if logger is None:
        return
    name = method if isinstance(method, str) and method else "response"
    logger.record_raw(
        "debug-bodies",
        _operational_log.encode_bodies_line(
            run_id=logger.run_id,
            direction=direction,
            method=name.replace("/", "."),
            body=raw.decode("utf-8", "replace").strip(),
        ),
    )


def _log() -> logging.Logger:
    global _logger
    if _logger is None:
        # Fallback before vault root is known — stderr only
        _logger = logging.getLogger("brain-proxy")
        if not _logger.handlers:
            _logger.setLevel(logging.DEBUG)
            h = logging.StreamHandler(sys.stderr)
            h.setLevel(logging.WARNING)
            _logger.addHandler(h)
    return _logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_proxy_version_from_disk(proxy_script: str) -> str | None:
    """Read PROXY_VERSION from proxy.py on disk via regex (not import)."""
    try:
        with open(proxy_script, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'^PROXY_VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


def _read_brain_version_from_disk(vault_root: str) -> str | None:
    """Read .brain-core/VERSION from vault root."""
    if vault_root is None:
        return None
    version_path = os.path.join(vault_root, ".brain-core", "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _make_error_response(msg_id: int | str | None, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _interface_changed_response(
    msg_id: int | str | None,
    reason: str,
    *,
    detail: str | None = None,
) -> dict:
    message = (
        "The Brain command interface changed while this call was in flight; "
        "re-discover tools and reformulate the request."
    )
    payload = {
        "schema": "brain.proxy-replay-result/1",
        "status": "error",
        "error": {
            "code": "interface_changed",
            "message": message,
            "effects": "none",
            "retryable": False,
            "details": {"reason": reason, "diagnostic": detail},
            "next_action": {
                "instruction": "rediscover_tools",
                "description": message,
            },
        },
    }
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": result_text_wire(f"interface_changed: {message}", payload),
            "structuredContent": payload,
            "isError": True,
        },
    }


def _outcome_unknown_response(
    record: AcceptedCallRecord,
    *,
    diagnostic: str | None = None,
) -> dict:
    """Return unknown execution after child loss, including admitted observations."""

    message = (
        "The Brain child was lost after accepting this call and no conclusive "
        "owned outcome could be obtained. Do not retry automatically; inspect the invocation outcome."
    )
    reference = {"invocation_id": record.invocation_id}
    payload = {
        "schema": "brain.command-result/1",
        "command": record.command_id,
        "command_version": record.command_version,
        "status": "error",
        "warnings": [],
        "result": None,
        "error": {
            "code": "command_outcome_unknown",
            "message": message,
            "details": {"reference": reference},
            "effects": "none" if record.mutation_class == "none" else "unknown",
            "retryable": False,
            "outcome_reference": reference,
            "next_action": {
                "command_id": "invocation.read",
                "arguments": [
                    {"name": "invocation_id", "value": record.invocation_id}
                ],
            },
        },
    }
    if diagnostic:
        _log().warning(
            "invocation outcome remains unknown id=%s diagnostic=%s",
            record.request_id,
            diagnostic,
        )
    return {
        "jsonrpc": "2.0",
        "id": record.request_id,
        "result": {
            "content": result_text_wire(
                f"{record.command_id}: command_outcome_unknown — {message}",
                payload,
            ),
            "structuredContent": payload,
            "isError": True,
        },
    }


def _resolved_outcome_response(
    record: AcceptedCallRecord,
    receipt: dict[str, object],
) -> dict:
    """Return a privacy-minimal conclusive receipt after child loss."""

    state = receipt["receipt"]["state"]
    execution = receipt["execution"]
    partial = execution == "partial"
    message = (
        f"The original {record.command_id} response was lost, but its durable "
        f"owned outcome records execution {execution} and effects {state}. The original result payload is unavailable."
    )
    payload = {
        "schema": "brain.proxy-outcome-resolution/1",
        "status": "partial" if partial else "resolved",
        "command": record.command_id,
        "command_version": record.command_version,
        "outcome_reference": {"invocation_id": record.invocation_id},
        "outcome": receipt,
        "retryable": False,
    }
    return {
        "jsonrpc": "2.0",
        "id": record.request_id,
        "result": {
            "content": result_text_wire(message, payload),
            "structuredContent": payload,
            "isError": execution != "succeeded",
        },
    }


def _parse_receipt_lookup_response(
    response: object,
    *,
    query_id: str,
    record: AcceptedCallRecord,
    invocation_read_version: int,
) -> dict[str, object] | None:
    """Validate the exact useful subset of an ``invocation.read`` MCP result."""

    if (not isinstance(response, dict) or response.get("id") != query_id
            or response.get("jsonrpc") != "2.0" or "error" in response):
        raise ValueError("receipt query response identity is invalid")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is not False:
        raise ValueError("receipt query did not return a successful MCP tool result")
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise ValueError("receipt query has no structured command result")
    expected_top = {
        "schema",
        "command",
        "command_version",
        "status",
        "warnings",
        "result",
        "committed_effects",
    }
    if set(structured) != expected_top:
        raise ValueError("receipt query command-result shape is invalid")
    if (
        structured["schema"] != "brain.command-result/1"
        or structured["command"] != "invocation.read"
        or structured["command_version"] != invocation_read_version
        or structured["status"] != "ok"
        or structured["warnings"] != []
        or structured["committed_effects"] != []
    ):
        raise ValueError("receipt query command-result facts are contradictory")
    payload = structured["result"]
    if not isinstance(payload, dict) or set(payload) != {"reference", "state", "intent", "outcome"}:
        raise ValueError("owned receipt lookup shape is invalid")
    reference = {"invocation_id": record.invocation_id}
    if payload["reference"] != reference:
        raise ValueError("receipt query returned the wrong outcome reference")
    intent, outcome = payload["intent"], payload["outcome"]
    admitted_at = None
    if intent is not None:
        fields = {"reference", "command_id", "command_version", "recorded_at", "basis", "generation", "source",
                  "grant_id", "operation_id", "operation_digest", "request_id", "permission_change"}
        if not isinstance(intent, dict) or set(intent) != fields:
            raise ValueError("admission intent shape is invalid")
        _validate_outcome_identity(intent, record, reference)
        admitted_at = _outcome_timestamp(intent["recorded_at"])
        if intent["source"] != "host-request" or intent["permission_change"] is not None:
            raise ValueError("MCP admission attribution is invalid")
        if not isinstance(intent["basis"], str) or intent["basis"] not in {"initial", "command", "operation"}:
            raise ValueError("admission basis is invalid")
        _outcome_identifier(intent["generation"])
        for name in ("grant_id", "operation_id", "request_id"):
            if intent[name] is not None:
                _outcome_identifier(intent[name])
        if (intent["basis"] == "initial") != (intent["grant_id"] is None):
            raise ValueError("admission grant contradicts its basis")
        digest = intent["operation_digest"]
        if digest is not None and (not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None):
            raise ValueError("admission operation digest is invalid")
        if intent["basis"] == "operation" and (intent["operation_id"] is None or digest is None):
            raise ValueError("specific admission has no complete operation binding")
        arguments = record.raw_request.get("params", {}).get("arguments", {})
        selector = arguments.get("brain_operation") if isinstance(arguments, dict) else None
        if selector is not None and (intent["basis"] != "operation" or intent["operation_id"] != selector):
            raise ValueError("admission differs from the explicitly selected operation")
    if payload["state"] == "still_unknown":
        if outcome is not None:
            raise ValueError("still-unknown lookup cannot carry a final outcome")
        return None
    if payload["state"] != "found" or admitted_at is None:
        raise ValueError("found outcome requires an owned admission intent")
    if not isinstance(outcome, dict) or set(outcome) != {"receipt", "execution", "config_revision"}:
        raise ValueError("final invocation outcome shape is invalid")
    if outcome["config_revision"] is not None:
        raise ValueError("MCP outcome cannot claim permission administration")
    receipt = outcome["receipt"]
    if not isinstance(receipt, dict) or set(receipt) != {
        "reference", "command_id", "command_version", "state", "recorded_at", "committed_effects"
    }:
        raise ValueError("outcome receipt shape is invalid")
    _validate_outcome_identity(receipt, record, reference)
    if _outcome_timestamp(receipt["recorded_at"]) < admitted_at:
        raise ValueError("final outcome predates its admission intent")
    allowed = {"succeeded": {"none", "committed"}, "failed": {"none"},
               "partial": {"known_partial"}, "unknown": {"none", "unknown"}}
    if (not isinstance(outcome["execution"], str) or outcome["execution"] not in allowed
            or not isinstance(receipt["state"], str) or receipt["state"] not in allowed[outcome["execution"]]):
        raise ValueError("execution state contradicts the effect outcome")
    effects = receipt["committed_effects"]
    if not isinstance(effects, list):
        raise ValueError("outcome receipt effects must be a list")
    for effect in effects:
        if (not isinstance(effect, dict) or set(effect) != {"kind", "subject"}
                or any(not isinstance(effect[key], str) or not effect[key].strip() for key in ("kind", "subject"))):
            raise ValueError("outcome receipt contains an invalid committed effect")
    if receipt["state"] == "known_partial" and not effects:
        raise ValueError("known-partial receipt must enumerate committed effects")
    if receipt["state"] in {"none", "unknown"} and effects:
        raise ValueError("none/unknown receipt cannot claim committed effects")
    return outcome


def _validate_outcome_identity(value, record, reference):
    if (value["reference"] != reference or value["command_id"] != record.command_id
            or type(value["command_version"]) is not int or value["command_version"] != record.command_version):
        raise ValueError("owned outcome contradicts the accepted call")


def _outcome_identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 128:
        raise ValueError("owned outcome identifier is invalid")


def _outcome_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("outcome timestamp is invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("outcome timestamp must be timezone-aware")
    return parsed


def _write_line(stream, obj: dict) -> None:
    """Serialize obj as NDJSON and write to stream."""
    line = json.dumps(obj) + "\n"
    stream.write(line.encode("utf-8"))
    stream.flush()


def _safe_write_line(stream, obj: dict, *, broken_pipe_message: str, error_message: str) -> bool:
    """Write one JSON-RPC line, logging and returning False if the client is gone."""
    try:
        _write_line(stream, obj)
        return True
    except BrokenPipeError:
        _log().warning(broken_pipe_message)
        return False
    except OSError as exc:
        _log().error("%s: %s", error_message, exc)
        return False


def _parse_jsonrpc_line(line: bytes, *, log_warning: bool) -> dict | None:
    """Parse one NDJSON JSON-RPC frame, returning None for invalid/non-object input."""
    try:
        obj = json.loads(line.decode("utf-8").strip())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if log_warning:
            _log().warning("failed to parse client message: %s", e)
        return None
    if not isinstance(obj, dict):
        if log_warning:
            _log().warning("ignored non-object JSON-RPC message: %r", obj)
        return None
    return obj


def _is_jsonrpc_request_id(value: object) -> bool:
    return isinstance(value, (int, str)) and not isinstance(value, bool)


def _restart_guidance_for_binding_error(exc: WorkspaceBindingError) -> str:
    return _GUIDANCE_BY_BINDING_CODE.get(exc.code, _GUIDANCE_BINDING)


def _human_text_index(content: list[object]) -> int | None:
    """Index of the user-facing text block, else the first unannotated text."""

    fallback = None
    for index, item in enumerate(content):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        annotations = item.get("annotations")
        audience = annotations.get("audience") if isinstance(annotations, dict) else None
        if audience == ["user"]:
            return index
        if fallback is None and not audience:
            fallback = index
    return fallback


def _decorate_with_drift_note(response: dict, old_ver: str, new_ver: str) -> dict:
    """
    Inject a proxy drift note into any outbound response — success or error.
    Notifications (no result/error) are returned unchanged.
    Brain results gain a model-visible warning and retain first-block JSON.
    Other text results and JSON-RPC errors retain their legacy human hint.
    Returns a (possibly modified) shallow copy.
    """
    note = (
        f"\n\nNote: MCP proxy has been upgraded ({old_ver} → {new_ver}). "
        f"Use {RESTART_TOOL}, or restart MCP, to load it with fresh exceptional consent."
    )
    # Success response with text content. Append to the human one-liner, never
    # the assistant JSON envelope, so first-block JSON stays parseable.
    result = response.get("result")
    envelope = result.get("structuredContent") if isinstance(result, dict) else None
    if isinstance(envelope, dict) and envelope.get("schema") == "brain.command-result/1":
        warning = {"code": "follow_up_required", "message": (
            f"MCP proxy {old_ver}; installed {new_ver}. Use {RESTART_TOOL} or restart MCP; consent resets. "
            f"{STATUS_TOOL} reports server refresh state."
        )}
        updated = {**envelope, "warnings": [*envelope.get("warnings", []), warning]}
        content = result.get("content", [])
        target = _human_text_index(content) if isinstance(content, list) else None
        concise = content[target].get("text", "") if target is not None else "Brain result"
        return {**response, "result": {**result, "structuredContent": updated,
                "content": result_text_wire(concise + note, updated)}}
    content = result.get("content", []) if isinstance(result, dict) else []
    if isinstance(content, list):
        target = _human_text_index(content)
        if target is not None:
            item = content[target]
            new_item = dict(item)
            new_item["text"] = new_item.get("text", "") + note
            new_content = list(content)
            new_content[target] = new_item
            modified = dict(response)
            modified["result"] = dict(result)
            modified["result"]["content"] = new_content
            return modified
    # Error response
    err = response.get("error")
    if isinstance(err, dict) and "message" in err:
        modified = dict(response)
        modified["error"] = dict(err)
        modified["error"]["message"] = err["message"] + note
        return modified
    return response


def _get_backoff_schedule() -> list[int]:
    """Return backoff schedule from env or default."""
    env = os.environ.get("BRAIN_PROXY_BACKOFF", "")
    if env.strip():
        try:
            return [int(x.strip()) for x in env.split(",")]
        except ValueError:
            pass
    return list(_DEFAULT_BACKOFF)


def _get_init_timeout() -> int:
    """Return child initialize timeout in seconds."""
    try:
        return int(os.environ.get("BRAIN_PROXY_INIT_TIMEOUT", "60"))
    except ValueError:
        return 60


def _get_read_timeout() -> int:
    """Return reader thread select timeout in seconds."""
    try:
        return int(os.environ.get("BRAIN_PROXY_READ_TIMEOUT", str(_READER_SELECT_TIMEOUT)))
    except ValueError:
        return _READER_SELECT_TIMEOUT


def _get_version_check_interval() -> float:
    """Return version-reset rate-limit interval in seconds."""
    try:
        return float(os.environ.get("BRAIN_PROXY_VERSION_CHECK_INTERVAL", str(_VERSION_CHECK_INTERVAL)))
    except ValueError:
        return float(_VERSION_CHECK_INTERVAL)


_LOG_BODIES = os.environ.get("BRAIN_LOG_BODIES", "").strip().lower() in {"1", "true"}


# ---------------------------------------------------------------------------
# ChildProcess
# ---------------------------------------------------------------------------

class ChildProcess:
    """Manages a single child server subprocess."""

    def __init__(self, python_path: str, server_target: str):
        self.python_path = python_path
        self.server_target = server_target
        self._proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stdout_reader: RawLineReader | None = None
        self.subscription_id: str | None = None
        self._send_lock = threading.Lock()

    def start(self, *, owner: ConsentOwner | None = None, owner_unavailable_code: str | None = None,
              transport_identity: ProcessIdentity | None = None, owner_initialisation_allowed: bool = False) -> None:
        """Spawn the child process."""
        env = without_owner_environment()
        if owner_unavailable_code is not None:
            if owner is not None or owner_unavailable_code not in OWNER_UNAVAILABLE_REASONS:
                raise ValueError("private owner availability does not match its channel")
            env[OWNER_UNAVAILABLE_ENV] = owner_unavailable_code
        env[PROXY_PROTOCOL_ENV] = str(PROXY_PROTOCOL)
        if os.path.isfile(self.server_target):
            cmd = [self.python_path, self.server_target]
        else:
            cmd = [self.python_path, "-m", self.server_target]
        channel = private_child_channel(owner, env) if owner is not None else nullcontext({"env": env})
        with channel as options:
            if transport_identity is not None:
                options["env"][PROCESS_CONTEXT_ENV] = transport_identity.launch_value(
                    initialise_owner=owner_initialisation_allowed)
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **options,
            )
        if sys.platform != "win32":
            self._stdout_reader = RawLineReader(self._proc.stdout.fileno())
        # Forward child stderr to proxy stderr in a background thread
        self._stderr_thread = threading.Thread(
            target=self._relay_stderr, daemon=True, name="child-stderr"
        )
        self._stderr_thread.start()
        _log().info(
            "child started: pid=%d cmd=%s",
            self._proc.pid, cmd,
        )
        _op_event("child.spawned", child_pid=self._proc.pid)

    def _relay_stderr(self) -> None:
        """Read child stderr and write to proxy stderr."""
        assert self._proc is not None
        try:
            for line in self._proc.stderr:
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()
        except Exception:
            pass

    def send(self, obj: dict) -> None:
        """Send a JSON object to the child's stdin."""
        assert self._proc is not None and self._proc.stdin is not None
        with self._send_lock:
            _write_line(self._proc.stdin, obj)

    def send_control(self, obj: dict, *, timeout: float = HANDOFF_TIMEOUT) -> None:
        """Bound bridge writes without another thread or interleaving frames.

        A partial frame cannot be withdrawn. On failure retire this child and
        let the existing recovery owner resolve any admitted work normally.
        Python 3.12 supports nonblocking pipe descriptors on Windows too.
        """
        assert self._proc is not None and self._proc.stdin is not None
        deadline = time.monotonic() + timeout
        if not self._send_lock.acquire(timeout=timeout):
            self.kill()
            raise TimeoutError("child control write lock timed out")
        fd = self._proc.stdin.fileno()
        blocking = None
        try:
            blocking = os.get_blocking(fd)
            os.set_blocking(fd, False)
            pending = memoryview((json.dumps(obj) + "\n").encode())
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("child control write timed out")
                try:
                    count = os.write(fd, pending[:READ_CHUNK])
                    if count == 0:
                        raise BrokenPipeError("child control pipe closed")
                    pending = pending[count:]
                except BlockingIOError:
                    if sys.platform == "win32":
                        # Windows select cannot wait for anonymous pipe writes.
                        time.sleep(min(.01, remaining))
                    else:
                        select.select([], [fd], [], remaining)
        except OSError:
            self.kill()
            raise
        finally:
            try:
                if blocking is not None:
                    os.set_blocking(fd, blocking)
            finally:
                self._send_lock.release()

    def readline(self) -> bytes | None:
        """Read one line from child stdout. Returns None on EOF."""
        assert self._proc is not None and self._proc.stdout is not None
        line = self._stdout_reader.readline() if self._stdout_reader else self._proc.stdout.readline()
        return line if line else None

    @property
    def line_ready(self) -> bool:
        return self._stdout_reader is not None and self._stdout_reader.line_ready

    def output_pending(self) -> bool:
        """Probe under the publication gate without treating EOF as pending data."""
        if self._stdout_reader and self._stdout_reader.remainder:
            return True
        if self.stdout_fd is None or not select.select([self.stdout_fd], [], [], 0)[0]:
            return False
        if self._stdout_reader is None:
            return True
        self._stdout_reader.remainder = os.read(self.stdout_fd, READ_CHUNK)
        return bool(self._stdout_reader.remainder)

    def wait(self) -> int | None:
        """Wait for child to exit. Returns exit code."""
        if self._proc is None:
            return None
        return self._proc.wait()

    def reap(self, timeout: float) -> None:
        if self._proc is not None:
            self._proc.wait(timeout=timeout)

    def kill(self) -> None:
        """Kill the child process."""
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass

    def poll(self) -> int | None:
        """Return exit code if exited, else None."""
        if self._proc is None:
            return None
        return self._proc.poll()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def stdout_fd(self) -> int | None:
        """Return the file descriptor of child stdout, or None."""
        if self._proc and self._proc.stdout:
            return self._proc.stdout.fileno()
        return None


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

class Proxy:
    """
    Main proxy logic. Owns the stdio channel (sys.stdin.buffer / sys.stdout.buffer).
    Spawns ChildProcess, forwards messages, handles restarts with backoff.
    """

    def __init__(self, python_path: str, server_target: str, vault_root: str | None, *, owner: ConsentOwner | None = None,
                 owner_unavailable_code: str | None = None, startup_failure: StartupFailure | None = None,
                 resolution_inputs: dict | None = None):
        self.python_path = python_path
        self.server_target = server_target
        self.vault_root = vault_root
        self._owner = owner
        self._owner_unavailable_code = owner_unavailable_code
        self._startup_failure = startup_failure
        self._resolution_inputs = resolution_inputs
        self._workspace = os.environ.get("BRAIN_WORKSPACE_DIR") or None
        self._subscriptions = Subscriptions()
        self._subscriptions_changed = threading.Event()
        self._pending_lifecycle = None
        self._preparing_child = None
        self._lifecycle_completion = queue.Queue(maxsize=1)
        self._wake_read = self._wake_write = None
        self._output_stream = None
        self._transport_identity = ProcessIdentity("mcp-instance", owner.identity.context_id if owner is not None else f"mcp-{uuid.uuid4()}")
        self._launch_lock = threading.Lock()
        self._child_has_launched = False
        self.proxy_script = os.path.abspath(__file__)

        self._child: ChildProcess | None = None
        self._child_lock = threading.Lock()

        # initialize capture
        self._init_request: dict | None = None
        self._init_response: dict | None = None
        self._interface_header: CommandInterfaceHeader | None = None
        self._interface_header_error: str | None = None
        self._advertised_tools: dict | None = None
        self._interface_lock = threading.Lock()
        self._client_protocol: str | None = None
        self._initial_protocol_selected = threading.Event()
        self._initial_protocol_lock = threading.Lock()
        self._public_session: dict | None = None
        self._publication_gate = threading.Lock()
        self._server_requests: dict[str, tuple[ChildProcess, int | str]] = {}
        self._handoff_state: dict | None = None
        self._handoff_error = "proxy_exec_failed"
        self._owner_close_timeout: float | None = None

        # Backoff state
        self._backoff_schedule = _get_backoff_schedule()
        self._backoff_slot = 0               # index into schedule
        self._child_start_time: float = 0.0  # monotonic time child was started
        self._gave_up = False
        self._last_launched_version: str | None = None

        # Proxy drift detection
        self._proxy_drift = False
        self._proxy_version_on_disk: str | None = None  # version read from disk
        self._installed_proxy_version: str | None = None
        self._proxy_file_hash = self._compute_proxy_hash()  # hash at startup

        # In-flight request tracking — maps request ID to (request, sent_at)
        # for requests forwarded to the child but not yet answered. sent_at is
        # a monotonic timestamp used to log per-message latency on response.
        # Protected by _inflight_lock.
        self._inflight_requests: dict[int | str, tuple[dict, float]] = {}
        self._accepted_calls: dict[int | str, AcceptedCallRecord] = {}
        self._inflight_lock = threading.Lock()
        # Frame pairing uses a per-run surrogate: client-chosen JSON-RPC ids can
        # be arbitrary strings and are never logged. Protected by _inflight_lock.
        self._frame_counter = itertools.count(1)
        self._frame_seqs: dict[int | str, int] = {}
        # _pending_replay holds drained-but-not-yet-replayed requests across
        # the wake/sleep boundary on a drift restart. Protected by
        # _restart_lock (NOT _inflight_lock — drain copies under _inflight_lock,
        # then hands the snapshot to recovery state under _restart_lock).
        self._pending_replay: list[dict] = []
        self._pending_accepted_calls: dict[int | str, AcceptedCallRecord] = {}
        self._pending_unexpected: list[dict] = []
        self._pending_unexpected_calls: dict[int | str, AcceptedCallRecord] = {}
        self._replay_depth = 0  # prevent infinite drift→replay loops

        # Synchronization
        self._child_ready = threading.Event()  # set when child is assigned
        self._recovery_trigger = threading.Event()
        self._restart_lock = threading.Lock()
        self._restart_in_progress = False
        self._recovery_exit_code: int | None = None
        self._version_reset_requested = False
        self._last_version_check: float = 0.0  # monotonic timestamp for cooldown
        self._refresh_blocked_version: str | None = None
        self._refresh_diagnostic: str | None = None
        self._refresh_header_rejected = False
        self._catalogue_generation = 0

        # Outbound message queue — all writes to sys.stdout.buffer go through here.
        # A single writer thread drains this queue, ensuring thread-safe writes
        # and centralised drift-note decoration. None is the shutdown sentinel.
        self._outbound: queue.Queue[dict | threading.Event | None] = queue.Queue()
        self._writer_thread_handle: threading.Thread | None = None
        self._reader_thread_handle: threading.Thread | None = None
        self._recovery_thread_handle: threading.Thread | None = None
        # Monotonic write-once: only ever flips False → True. Concurrent
        # setters are safe because every writer is setting the same value.
        self._recovery_thread_failed = False

        # Shutdown flag
        self._shutdown = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _start_writer_loop(self) -> None:
        """Start the client-writer thread once."""
        if self._writer_thread_handle is not None:
            return
        writer = threading.Thread(
            target=self._writer_thread, daemon=True, name="client-writer"
        )
        writer.start()
        self._writer_thread_handle = writer

    def _start_recovery_loop(self) -> None:
        """Start the child-recovery thread once."""
        if self._recovery_thread_handle is not None:
            return
        recovery = threading.Thread(
            target=self._run_recovery_thread, daemon=True, name="child-recovery"
        )
        recovery.start()
        self._recovery_thread_handle = recovery

    def _start_reader_loop(self) -> None:
        """Start the child-reader thread once."""
        if self._reader_thread_handle is not None:
            return
        reader = threading.Thread(
            target=self._reader_thread, daemon=True, name="child-reader"
        )
        reader.start()
        self._reader_thread_handle = reader

    def _ensure_background_threads(self) -> None:
        """Start background threads if they have not been started yet."""
        self._start_writer_loop()
        self._start_recovery_loop()
        self._start_reader_loop()

    def _recovery_thread_is_dead(self) -> bool:
        """True if the recovery thread was started but is no longer running."""
        handle = self._recovery_thread_handle
        return handle is not None and not handle.is_alive()

    def _start_child(self) -> bool:
        """
        Validate the child interface before publishing it to the relay.
        Returns True on success, False on failure (timeout or crash).
        """
        if self._assess_startup() is not None or self._runtime_error() is not None:
            return False
        installed_version = _read_brain_version_from_disk(self.vault_root)
        child = ChildProcess(self.python_path, self.server_target)
        self._preparing_child = child
        try:
            with self._launch_lock:
                try:
                    child.start(owner=self._owner, owner_unavailable_code=self._owner_unavailable_code,
                                transport_identity=self._transport_identity,
                                owner_initialisation_allowed=self._owner is not None and not self._child_has_launched)
                finally:
                    if child.pid is not None:
                        self._child_has_launched = True
        except Exception as e:
            _log().error("failed to start child process: %s", e)
            return False
        # Check for proxy drift
        self._check_proxy_drift()

        if self._client_protocol == "modern" and not self._discover_child(child):
            return False

        # Replay initialize only after the first session has completed it
        # (init_response captured) — otherwise the bootstrap request would be
        # sent twice: once here and again by the main loop.
        if self._init_request is not None and self._init_response is not None:
            try:
                child.send(self._init_request)
            except Exception as e:
                _log().error("failed to send initialize to new child: %s", e)
                child.kill()
                return False

            # Read response with timeout
            response = self._read_with_timeout(child, _get_init_timeout())
            if response is None:
                _log().error("child init timeout or crash during restart")
                child.kill()
                return False

            response = add_control_discovery(response, self._init_request)
            if self._public_session is not None and public_session(response) != self._public_session:
                self._interface_header_error = "Replacement changed the negotiated public MCP session; restart MCP."
                child.kill()
                return False
            if not self._capture_interface_header(response):
                child.kill()
                return False
            child.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            _log().info("child restarted successfully, discarding init response")

        # The reader cannot race these proxy-owned receipt queries because
        # the replacement child is not published until resolution ends.
        self._resolve_pending_unexpected(child)

        if (installed_version != _read_brain_version_from_disk(self.vault_root)
                or self._runtime_error() is not None or self._assess_startup() is not None):
            child.kill()
            return False
        try:
            self._sync_subscription_child(child, negotiated=True)
        except OSError:
            child.kill()
            return False
        if not self._publication_gate.acquire(timeout=HANDOFF_TIMEOUT):
            child.kill()
            return False
        try:
            if self._shutdown or (self._pending_lifecycle is not None and self._pending_lifecycle["cancelled"]):
                child.kill()
                return False
            with self._restart_lock:
                pending = self._pending_lifecycle
                if self._shutdown or pending is not None and (pending["cancelled"] or pending["generation"] != self._catalogue_generation):
                    child.kill()
                    return False
                with self._child_lock:
                    previous = self._child
                    with self._inflight_lock:
                        if any(entry[0] is previous for entry in self._server_requests.values()):
                            child.kill()
                            return False
                        self._child = child
                        self._preparing_child = None
                        if pending is not None:
                            pending["activated"] = True
            self._child_start_time = time.monotonic()
            self._last_launched_version = installed_version
            self._catalogue_generation += 1
            if previous is not None:
                previous.kill()
            if self._init_response is not None or self._client_protocol == "modern":
                self._publish_catalogue_change()
            self._child_ready.set()
            return True
        finally:
            self._publication_gate.release()

    def _discover_child(self, child: ChildProcess) -> bool:
        """Negotiate a modern child before its stdout reader is enabled."""

        discovery_id = f"brain-proxy-discover-{uuid.uuid4()}"
        try:
            child.send(_internal_request("server/discover", discovery_id, {}, modern=True))
            response = self._read_internal_response(child, discovery_id, _get_init_timeout())
            if self._public_session is not None and public_session(response) != self._public_session:
                self._interface_header_error = "Replacement changed the public MCP discovery contract; restart MCP."
            elif self._capture_interface_header(response):
                self._public_session = public_session(response)
                return True
        except (OSError, ValueError) as exc:
            _log().error("child interface discovery failed: %s", exc)
        child.kill()
        return False

    def _establish_initial_protocol(self, child: ChildProcess) -> bool:
        """Negotiate modern discovery before releasing the sole child reader."""
        with self._initial_protocol_lock:
            if self._initial_protocol_selected.is_set():
                return child.poll() is None and (self._client_protocol != "modern" or self._interface_header is not None)
            ready = self._client_protocol != "modern" or self._discover_child(child)
            if ready:
                try:
                    self._sync_subscription_child(child, negotiated=True)
                except OSError:
                    ready = False
            self._initial_protocol_selected.set()
            if not ready:
                self._signal_recovery(1, child=child)
            return ready

    def _send_to_client(self, obj: dict) -> None:
        """Enqueue a message for the client. Thread-safe. Never raises."""
        self._outbound.put(obj)

    def _publish_catalogue_change(self) -> None:
        event = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        if self._client_protocol == "modern":
            self._subscriptions.emit(event, self._send_to_client)
        else:
            self._send_to_client(event)

    def _sync_subscription_child(self, child, *, negotiated: bool = False) -> None:
        if self._client_protocol != "modern" or self.server_target != "brain_mcp.server":
            return
        if child is None or child.poll() is not None:
            return
        if not negotiated and not self._initial_protocol_selected.is_set():
            return
        filters = {}
        for stream in self._subscriptions.snapshot():
            for key, value in stream["notifications"].items():
                if key == "resourceSubscriptions":
                    filters[key] = list(dict.fromkeys([*filters.get(key, []), *value]))
                else:
                    filters[key] = value
        previous = child.subscription_id
        child.subscription_id = f"brain-proxy-subscription-{uuid.uuid4()}" if filters else None
        if previous:
            child.send_control({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": previous}})
        if child.subscription_id:
            child.send_control(_internal_request("subscriptions/listen", child.subscription_id,
                                         {"notifications": filters}, modern=True))

    def _request_subscription_sync(self) -> None:
        """Let the existing worker own potentially blocking child pipe writes."""
        self._subscriptions_changed.set()
        self._recovery_trigger.set()

    def _assess_startup(self) -> str | None:
        if self.vault_root is None:
            return "target_unavailable"
        if self._resolution_inputs is None:
            return self._startup_failure.code if self._startup_failure else None
        try:
            target = resolve_brain_target(**self._resolution_inputs)
            if (target.vault_root, target.workspace_dir) != (self.vault_root, self._workspace):
                self._startup_failure = StartupFailure("target", "target_changed", "Selected Brain/workspace changed; reconnect MCP after explicit configuration.")
                return self._startup_failure.code
            _probe_local_state(self.vault_root)
        except (WorkspaceBindingError, OSError) as exc:
            self._startup_failure = StartupFailure("assessment", "startup_unavailable", str(exc)[:240])
            return self._startup_failure.code
        self._startup_failure = None
        return None

    def _recover_owed_client(
        self,
        msg_id,
        child: ChildProcess,
        exit_code: int,
        is_request: bool,
    ) -> None:
        """Recover a failed child send and answer a request still owed by this thread."""
        owned_recovery = self._signal_recovery(exit_code, child=child)
        if not owned_recovery and is_request:
            # Reader thread already drove recovery; if its drain ran before
            # our inflight insert, our id is still tracked and the client is
            # owed a response.
            with self._inflight_lock:
                was_tracked = self._inflight_requests.pop(msg_id, None) is not None
                accepted = self._accepted_calls.pop(msg_id, None)
                self._frame_seqs.pop(msg_id, None)
            if was_tracked:
                if accepted is None:
                    self._send_to_client(self._error_response_for_dead_child(msg_id))
                else:
                    self._send_to_client(
                        _outcome_unknown_response(
                            accepted,
                            diagnostic="recovery was already claimed before dispatch failed",
                        )
                    )

    def _initiate_shutdown(self) -> None:
        """Begin proxy shutdown and wake any sleeping background threads."""
        with self._restart_lock:
            self._shutdown = True
        self._wake_input()
        self._subscriptions.clear()
        if self._preparing_child is not None:
            self._preparing_child.kill()
        self._child_ready.set()
        self._recovery_trigger.set()
        if self._owner is not None:
            try:
                self._owner.close(lock_timeout=self._owner_close_timeout)
            except (OSError, MutationLockError):
                if self._owner_close_timeout is None:
                    raise
                _log().error("owner ended; private directory cleanup pending: %s", self._owner.private_directory)

    def _wake_input(self) -> None:
        if self._wake_write is not None:
            try:
                os.write(self._wake_write, b"1")
            except (BlockingIOError, OSError):
                pass

    def _writer_thread(self) -> None:
        """
        Single thread that owns sys.stdout.buffer writes.
        Drains self._outbound, applies drift decoration, writes to stdout.
        Stops on None sentinel or BrokenPipeError.
        """
        while True:
            obj = self._outbound.get()
            if obj is None:
                break
            if isinstance(obj, threading.Event):
                obj.set()
                continue
            event = obj if isinstance(obj, SubscriptionEvent) else None
            if event:
                if not self._subscriptions.current(event.key):
                    self._subscriptions.delivered(event.key)
                    continue
                obj = event.frame
            if self._proxy_drift and self._proxy_version_on_disk:
                obj = _decorate_with_drift_note(obj, PROXY_VERSION, self._proxy_version_on_disk)
            if not _safe_write_line(
                self._output_stream if self._output_stream is not None else sys.stdout.buffer,
                obj,
                broken_pipe_message="client disconnected (broken pipe on stdout)",
                error_message="error writing to client stdout",
            ):
                self._initiate_shutdown()
                return
            if event:
                self._subscriptions.delivered(event.key)

    def _read_with_timeout(self, child: ChildProcess, timeout: int) -> dict | None:
        """
        Read one JSON line from child stdout within timeout seconds.
        Returns parsed dict or None on timeout/error.
        """
        line = _read_child_line_with_timeout(child, timeout)
        if line is None:
            _log().warning(
                "child initialize produced no response within %ds or closed stdout",
                timeout,
            )
            return None
        obj = _parse_jsonrpc_line(line, log_warning=False)
        if obj is None:
            _log().error("error reading child initialize response: invalid JSON-RPC frame")
            return None
        return obj

    def _capture_interface_header(self, response: dict) -> bool:
        """Publish one validated child header or its fail-closed parse error."""

        try:
            header = interface_header_from_response(response)
            if header.interface_epoch != 3:
                raise ValueError("child command interface epoch is incompatible")
            if not (
                header.minimum_proxy_protocol
                <= PROXY_PROTOCOL
                <= header.maximum_proxy_protocol
            ):
                raise ValueError("child does not support the running proxy protocol")
        except (TypeError, ValueError) as exc:
            with self._interface_lock:
                self._interface_header = None
                self._interface_header_error = str(exc)
            _log().error("child command-interface header rejected: %s", exc)
            _op_event("interface.rejected")
            return False
        with self._interface_lock:
            self._interface_header = header
            self._interface_header_error = None
            if self._advertised_tools is None:
                self._advertised_tools = dict(header.tools)
        _log().info(
            "accepted child command-interface header epoch=%d fingerprint=%s",
            header.interface_epoch,
            header.fingerprint,
        )
        _op_event("interface.accepted", interface_epoch=header.interface_epoch)
        return True

    def _compute_proxy_hash(self, content: bytes | None = None) -> str | None:
        """Compute SHA-256 hash prefix of proxy.py. Uses provided content or reads from disk."""
        try:
            if content is None:
                with open(self.proxy_script, "rb") as f:
                    content = f.read()
            return hashlib.sha256(content).hexdigest()[:12]
        except OSError:
            return None

    def _check_proxy_drift(self) -> None:
        """Read proxy file from disk once, check version string and hash."""
        self._installed_proxy_version = None
        try:
            with open(self.proxy_script, "rb") as f:
                content = f.read()
        except OSError:
            return
        # Extract version string
        m = re.search(
            rb'^PROXY_VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE,
        )
        if not m:
            return
        on_disk = m.group(1).decode("utf-8")
        self._installed_proxy_version = on_disk
        if on_disk != PROXY_VERSION:
            if not self._proxy_drift:
                _log().warning(
                    "proxy drift detected: running=%s disk=%s", PROXY_VERSION, on_disk
                )
            self._proxy_drift = True
            self._proxy_version_on_disk = on_disk
            return
        # Version strings match — fall back to file hash comparison
        disk_hash = self._compute_proxy_hash(content)
        if (
            disk_hash is not None
            and self._proxy_file_hash is not None
            and disk_hash != self._proxy_file_hash
        ):
            if not self._proxy_drift:
                _log().warning(
                    "proxy drift detected via hash: running=%s disk=%s",
                    self._proxy_file_hash, disk_hash,
                )
            self._proxy_drift = True
            self._proxy_version_on_disk = f"{PROXY_VERSION}+modified"
        else:
            self._proxy_drift = False
            self._proxy_version_on_disk = None

    # ------------------------------------------------------------------
    # Backoff / restart logic
    # ------------------------------------------------------------------

    def _should_reset_backoff(self) -> bool:
        """True if child has been alive long enough to reset backoff."""
        return (
            self._child_start_time > 0
            and time.monotonic() - self._child_start_time >= _CHILD_ALIVE_RESET_SECS
        )

    def _finish_recovery_cycle(self) -> None:
        """Clear the active recovery marker after a recovery attempt ends."""
        with self._restart_lock:
            self._restart_in_progress = False
            self._recovery_exit_code = None
            self._version_reset_requested = False

    def _fail_pending_replay(self, message: str) -> None:
        """Fail any saved recovery work when the child cannot restart."""
        with self._restart_lock:
            pending = list(self._pending_replay)
            self._pending_replay.clear()
            self._pending_accepted_calls.clear()
            unexpected = list(self._pending_unexpected)
            accepted = dict(self._pending_unexpected_calls)
            self._pending_unexpected.clear()
            self._pending_unexpected_calls.clear()
        if pending:
            self._replay_depth = 0
            self._send_client_errors(pending, message)
        for request in unexpected:
            record = accepted.get(request.get("id"))
            if record is None:
                self._send_client_errors([request], message)
            else:
                self._send_to_client(
                    _outcome_unknown_response(record, diagnostic=message)
                )

    def _replay_pending_requests(self) -> None:
        """Replay saved requests to the current child before ending recovery."""
        with self._restart_lock:
            pending = list(self._pending_replay)
            self._pending_replay.clear()
            accepted = dict(self._pending_accepted_calls)
            self._pending_accepted_calls.clear()
        if pending:
            self._replay_requests(pending, accepted)

    def _resolve_pending_unexpected(self, child: ChildProcess) -> None:
        """Resolve accepted calls after child loss without semantic replay."""

        with self._restart_lock:
            pending = list(self._pending_unexpected)
            accepted = dict(self._pending_unexpected_calls)
            self._pending_unexpected.clear()
            self._pending_unexpected_calls.clear()
        if not pending:
            return

        with self._interface_lock:
            header = self._interface_header
            header_error = self._interface_header_error
        for request in pending:
            request_id = request.get("id")
            record = accepted.get(request_id)
            if record is None:
                self._send_client_errors([request], "accepted-call record was lost")
                continue
            self._resolve_owned_orphan(child, record, header, header_error)

    def _resolve_owned_orphan(
        self,
        child: ChildProcess,
        record: AcceptedCallRecord,
        header: CommandInterfaceHeader | None,
        header_error: str | None,
    ) -> None:
        """Query an owned outcome for any accepted call without re-executing it."""

        if header is None:
            self._send_to_client(
                _outcome_unknown_response(record, diagnostic=header_error)
            )
            return
        mapping = header.tool("invocation_read")
        if mapping is None or mapping.command_id != "invocation.read":
            self._send_to_client(
                _outcome_unknown_response(
                    record,
                    diagnostic="replacement interface cannot query invocation receipts",
                )
            )
            return
        query_id = f"brain-proxy-receipt-{uuid.uuid4()}"
        query = _internal_request(
            "tools/call", query_id, {
                "name": "invocation_read",
                "arguments": {"invocation_id": record.invocation_id},
            },
            modern=self._client_protocol == "modern",
        )
        try:
            _query_record, query = accept_call(
                query, header,
                invocation_id=f"mcp-{uuid.uuid4()}",
                accepted_at=datetime.now(timezone.utc),
            )
            child.send(query)
            response = self._read_internal_response(child, query_id, _get_init_timeout())
            receipt = _parse_receipt_lookup_response(
                response,
                query_id=query_id,
                record=record,
                invocation_read_version=mapping.command_version,
            )
        except Exception as exc:
            _log().warning(
                "invocation outcome query inconclusive id=%s: %s",
                record.request_id,
                exc,
            )
            self._send_to_client(
                _outcome_unknown_response(record, diagnostic=str(exc))
            )
            return
        if receipt is None or receipt["execution"] == "unknown":
            self._send_to_client(
                _outcome_unknown_response(
                    record,
                    diagnostic="outcome receipt remains inconclusive",
                )
            )
            return
        self._send_to_client(_resolved_outcome_response(record, receipt))

    def _read_internal_response(
        self,
        child: ChildProcess,
        query_id: str,
        timeout: int,
    ) -> dict:
        """Read one proxy-owned child response while preserving notifications."""

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("internal request timed out")
            response = self._read_with_timeout(child, max(1, int(remaining + 0.999)))
            if response is None:
                raise ValueError("internal request produced no response")
            if response.get("id") == query_id:
                return response
            if response.get("method"):
                if "id" in response:
                    raise ValueError("candidate requested host input during private negotiation")
                if response["method"] in EVENT_FILTERS:
                    # Catalogue activation is published after validation;
                    # private negotiation is not a host subscription stream.
                    continue
                self._send_to_client(response)
                continue
            raise ValueError("internal request received a contradictory response identifier")

    def _signal_recovery(
        self,
        exit_code: int | None,
        *,
        child: ChildProcess | None = None,
    ) -> bool:
        """
        Record child loss and wake the recovery thread.

        Returns True if this call claimed the recovery work.
        """
        if exit_code is None:
            exit_code = 1

        if not self._proxy_drift:
            self._check_proxy_drift()

        with self._restart_lock:
            if self._shutdown or self._restart_in_progress:
                return False

            with self._child_lock:
                if child is not None:
                    if self._child is not child:
                        return False
                    self._child = None
                elif self._child is not None:
                    return False

            self._child_ready.clear()
            self._restart_in_progress = True
            self._recovery_exit_code = exit_code

            is_drift = exit_code == _EXIT_CODE_VERSION_DRIFT
            can_replay = is_drift and self._replay_depth < _MAX_REPLAY_DEPTH
            with self._inflight_lock:
                self._server_requests.clear()
            drained_requests, accepted_calls = self._drain_inflight()

            # Even a drift exit only proves the guard stopped one request, not
            # that every concurrent accepted call was unentered. Recover all
            # semantic calls from owned receipts; replay only protocol traffic.
            self._pending_unexpected = [request for request in drained_requests
                                        if request.get("id") in accepted_calls]
            self._pending_unexpected_calls = accepted_calls
            self._pending_accepted_calls = {}
            self._pending_replay = ([request for request in drained_requests
                                     if request.get("id") not in accepted_calls]
                                    if can_replay else [])
            self._replay_depth = self._replay_depth + 1 if self._pending_replay else 0

        recovery_dead = self._recovery_thread_is_dead() or self._recovery_thread_failed
        legacy_orphans = [
            request
            for request in drained_requests
            if request.get("id") not in accepted_calls
        ]
        if not can_replay and legacy_orphans:
            # When recovery cannot proceed, the orphan response must reflect
            # that — the soft "restarting" message would mislead the client
            # into retrying against a proxy that will never recover.
            message = (
                _RECOVERY_THREAD_CRASHED_MSG if recovery_dead
                else "server exited mid-request, restarting"
            )
            self._send_client_errors(legacy_orphans, message)

        self._recovery_trigger.set()

        # If the recovery thread is no longer running, set() above wakes
        # nobody — anything we just queued for replay would sit forever in
        # _pending_replay. Fail it now so the client gets a response, and
        # mark the proxy as unrecoverable so subsequent requests do too.
        # Gated on (pending_replay populated OR first detection) so steady-
        # state dead-recovery hits don't keep re-acquiring _restart_lock.
        if recovery_dead and (
            self._pending_replay
            or self._pending_unexpected
            or not self._recovery_thread_failed
        ):
            self._recovery_thread_failed = True
            self._fail_pending_replay(_RECOVERY_THREAD_CRASHED_MSG)

        return True

    def _signal_version_reset(self) -> None:
        """Ask the recovery thread to re-check VERSION after give-up."""
        with self._restart_lock:
            if self._shutdown or not self._gave_up:
                return
            self._version_reset_requested = True
        self._recovery_trigger.set()

    def _required_runtime_python(self) -> str:
        return _installed_runtime_python(self.vault_root)

    def _runtime_status(self) -> dict:
        """Keep the proxy and child in the same installed dependency environment."""
        loaded = sys.executable
        if self.vault_root is None:
            return {"loaded": loaded, "child": self.python_path, "required": None,
                    "state": "installation_unavailable", "restart_required": True}
        if self.server_target != "brain_mcp.server":
            return {"loaded": loaded, "child": self.python_path, "required": None,
                    "state": "unmanaged", "restart_required": False}
        try:
            required = self._required_runtime_python()
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"loaded": loaded, "child": self.python_path, "required": None,
                    "state": "installation_unavailable", "restart_required": True}
        drift = not (same_executable_path(loaded, required)
                     and same_executable_path(self.python_path, required))
        return {"loaded": loaded, "child": self.python_path, "required": required,
                "state": "restart_required" if drift else "current", "restart_required": drift}

    def _runtime_error(self) -> str | None:
        runtime = self._runtime_status()
        if runtime["state"] == "installation_unavailable":
            return "runtime_installation_unavailable"
        return "runtime_restart_required" if runtime["restart_required"] else None

    def _proxy_status(self) -> dict:
        """Observe transport state without asking the application child."""
        self._check_proxy_drift()
        runtime = self._runtime_status()
        installed = _read_brain_version_from_disk(self.vault_root)
        child = self._get_child()
        alive = child is not None and child.poll() is None
        with self._interface_lock:
            header = self._interface_header
        if runtime["restart_required"]:
            state = "runtime_restart_required"
        elif self._restart_in_progress:
            state = "refreshing"
        elif self._refresh_blocked_version == installed and installed is not None:
            state = "blocked"
        elif not alive:
            state = "unavailable"
        elif installed is None:
            state = "installation_unavailable"
        elif installed != self._last_launched_version:
            state = "available"
        else:
            state = "current"
        status = {
            "proxy": {"loaded": PROXY_VERSION,
                      "installed": self._installed_proxy_version,
                      "restart_required": self._proxy_drift or runtime["restart_required"]},
            "runtime": runtime,
            "server": {"loaded": self._last_launched_version, "installed": installed,
                       "available": alive, "refresh": state},
            "interface": {"proxy_protocol": PROXY_PROTOCOL,
                          "epoch": header.interface_epoch if header else None,
                          "catalogue_fingerprint": header.catalogue_fingerprint if header else None,
                          "generation": self._catalogue_generation,
                          "installed_compatibility": (
                              "rejected" if state == "blocked" and self._refresh_header_rejected else
                              "validated" if state == "current" and header else "unverified")},
            "diagnostic": (
                "MCP must be restarted to load the installed managed runtime. "
                "New application calls have no effects; already in-flight work may finish. "
                "Use brain_proxy_restart when idle, or restart MCP in the host. "
                "If the installed runtime is unavailable, repair it before restarting."
                if runtime["restart_required"] else
                self._refresh_diagnostic if state == "blocked" else None),
            "consent": "same-proxy; new proxy requires fresh exceptional consent",
            "next_action": ("restart_mcp" if runtime["state"] == "installation_unavailable" or (runtime["restart_required"] and not alive) else
                            RESTART_TOOL if (self._proxy_drift or runtime["restart_required"]) and os.name == "posix" else
                            "restart_mcp" if runtime["restart_required"] else
                            "restart_mcp" if self._proxy_drift or (state == "blocked" and self._refresh_header_rejected) else
                            REFRESH_TOOL if state in {"available", "unavailable", "blocked"} else
                            STATUS_TOOL if state == "refreshing" else None),
        }
        if self._startup_failure:
            status["diagnostic"] = self._startup_failure.detail
            status["next_action"] = "restart_mcp" if self.vault_root is None or self._startup_failure.code == "target_changed" else RESTART_TOOL
        status["lifecycle"] = {"phase": "stopping" if self._shutdown else
                               "recovering" if self._pending_lifecycle or self._restart_in_progress else
                               "ready" if alive and header else "blocked",
                               "failure": self._startup_failure.code if self._startup_failure else None}
        return status

    def _transport_state(self, request_id, stdin: RawLineReader) -> dict:
        return {
            "version": HANDOFF_VERSION, "pid": os.getpid(), "vault": self.vault_root,
            "workspace": os.environ.get("BRAIN_WORKSPACE_DIR"),
            "python": self.python_path, "server": self.server_target,
            "protocol": self._client_protocol, "initialise_request": self._init_request,
            "initialise_response": self._init_response, "public_session": self._public_session,
            "tools": {name: asdict(tool) for name, tool in (self._advertised_tools or {}).items()},
            "generation": self._catalogue_generation, "request_id": request_id,
            "remainder": base64.b64encode(stdin.remainder).decode("ascii"),
            "subscriptions": self._subscriptions.snapshot(),
            "resolution": ({**self._resolution_inputs, "start_dir": str(self._resolution_inputs["start_dir"])}
                           if self._resolution_inputs is not None else None),
        }

    def _restore_transport(self, state: dict) -> None:
        self._client_protocol = state["protocol"]
        self._init_request = state["initialise_request"]
        self._init_response = state["initialise_response"]
        self._public_session = state["public_session"]
        self._advertised_tools = {name: InterfaceTool(**tool) for name, tool in state["tools"].items()}
        self._catalogue_generation = state["generation"]
        self._subscriptions = Subscriptions.restore(state.get("subscriptions", []))
        self._resolution_inputs = state.get("resolution")
        if self._resolution_inputs is not None:
            self._resolution_inputs = {**self._resolution_inputs, "start_dir": Path(self._resolution_inputs["start_dir"])}

    def _handoff_environment(self) -> dict:
        env = without_owner_environment()
        env["BRAIN_VAULT_ROOT"] = self.vault_root
        env["PYTHONPATH"] = str(Path(self.vault_root) / ".brain-core")
        return env

    def _preflight_handoff(self, fd: int, python: str) -> bool:
        env = self._handoff_environment()
        env.pop("BRAIN_OPERATOR_KEY", None)
        process = subprocess.Popen(
            [python, "-m", "brain_mcp.proxy", "--check-handoff", str(fd)],
            env=env, pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        try:
            return process.wait(timeout=HANDOFF_TIMEOUT) == 0
        except subprocess.TimeoutExpired:
            return False
        finally:
            # Includes the probe's server if import or negotiation timed out.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=HANDOFF_TIMEOUT)

    def _replace_idle_image(self, fd: int, state: dict, *, image_hash: str | None = None) -> str | None:
        # The reader owns each complete publication, including the gap between
        # removing an in-flight request and queuing its response.
        if not self._publication_gate.acquire(timeout=HANDOFF_TIMEOUT):
            return "server_busy"
        child = None
        frozen = False
        try:
            with self._restart_lock:
                if self._restart_in_progress or self._shutdown:
                    return "refresh_in_progress"
                with self._inflight_lock:
                    if self._inflight_requests or self._server_requests:
                        return "server_busy"
                child = self._get_child()
                if child is not None and child.poll() is None:
                    os.kill(child.pid, signal.SIGSTOP)
                    frozen = True
                    deadline = time.monotonic() + HANDOFF_TIMEOUT
                    while True:
                        pid, status = os.waitpid(child.pid, os.WUNTRACED | os.WNOHANG)
                        if pid:
                            if not os.WIFSTOPPED(status):
                                return "server_unavailable"
                            break
                        if time.monotonic() >= deadline:
                            return "server_busy"
                        time.sleep(0.005)
                # No producer can now publish after the barrier. Even an
                # incomplete pending frame causes refusal, never truncation.
                if child is not None and child.output_pending():
                    return "server_busy"
                barrier = threading.Event()
                self._outbound.put(barrier)
                if not barrier.wait(HANDOFF_TIMEOUT):
                    return "proxy_output_busy"
                if image_hash is not None and (self._compute_proxy_hash() != image_hash
                        or not same_executable_path(state["python"], self._required_runtime_python())):
                    return "installation_changed"
                self._owner_close_timeout = HANDOFF_TIMEOUT
                self._shutdown = True
                self._child_ready.set()
                self._recovery_trigger.set()
                if child is not None:
                    child.kill()
                frozen = False
        finally:
            if frozen:
                try:
                    os.kill(child.pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            self._publication_gate.release()

        if child is not None:
            child.reap(HANDOFF_TIMEOUT)

        # Readers cannot enqueue after shutdown; the acknowledged barrier means
        # the writer is no longer blocked on host output.
        self._outbound.put(None)
        for thread in (self._reader_thread_handle, self._recovery_thread_handle, self._writer_thread_handle):
            if thread is not None:
                thread.join(timeout=HANDOFF_TIMEOUT)
                if thread.is_alive():
                    raise RuntimeError("proxy handoff could not stop an output owner")
        if self._owner is not None:
            try:
                self._owner.close(lock_timeout=HANDOFF_TIMEOUT)
            except (MutationLockError, OSError):
                _log().error("handoff ended consent but private owner directory cleanup is pending: %s", self._owner.private_directory)
                self._handoff_state = state
                self._handoff_error = "proxy_owner_cleanup_pending"
                return self._handoff_error
        logger = _operational_log.current_logger()
        if logger is not None:
            logger.close(exit_code=0)
        try:
            changed = image_hash is not None and (
                self._compute_proxy_hash() != image_hash
                or not same_executable_path(state["python"], self._required_runtime_python()))
        except (OSError, ValueError, subprocess.SubprocessError):
            # Retirement has ended consent and output ownership. Preserve the
            # retained transport so its fresh owner can answer the restart.
            self._handoff_state = state
            self._handoff_error = "runtime_installation_unavailable"
            return self._handoff_error
        if changed:
            self._handoff_state = state
            self._handoff_error = "installation_changed"
            return self._handoff_error
        try:
            os.execve(state["python"],
                      [state["python"], "-m", "brain_mcp.proxy", "--handoff-fd", str(fd)],
                      self._handoff_environment())
        except OSError:
            # The composition root creates a new owner and serves the retained
            # stdio in this image. The old consent context is never revived.
            self._handoff_state = state
            return "proxy_exec_failed"

    def _admit_lifecycle(self, request: dict, stdin, *, resume: bool = False) -> str | None:
        with self._restart_lock:
            if self._pending_lifecycle is not None or self._restart_in_progress:
                return "refresh_in_progress"
            with self._inflight_lock:
                if self._inflight_requests or self._server_requests:
                    return "server_busy"
            if self._recovery_thread_failed or self._recovery_thread_is_dead():
                return "proxy_restart_required"
            if self._client_protocol == "legacy" and self._init_response is None:
                return "not_initialised"
            state = self._transport_state(request["id"], stdin) if isinstance(stdin, RawLineReader) else None
            self._pending_lifecycle = {"request": request, "resume": resume, "state": state,
                                       "generation": self._catalogue_generation, "cancelled": False}
            self._restart_in_progress = True
        self._recovery_trigger.set()
        return None

    def _complete_lifecycle(self, pending, code, *, prepared=None):
        if self._shutdown:
            return
        if pending["cancelled"]:
            code, prepared = "request_cancelled", None
        if prepared is not None:
            pending["prepared"] = True
            self._lifecycle_completion.put((pending, prepared))
            self._wake_input()
            return
        request = pending["request"]
        if pending["resume"] and code is None:
            try:
                with self._publication_gate:
                    if self._shutdown:
                        return
                    forwarded, accepted = self._prepare_interface_call(request)
                    self._forward_to_child(forwarded, self._get_child(), accepted)
            except (ValueError, TypeError) as exc:
                self._refuse_interface_replay(request["id"], "accepted_call_invalid", detail=str(exc))
        with self._restart_lock:
            self._restart_in_progress = False
            if self._pending_lifecycle is pending:
                self._pending_lifecycle = None
        if not pending["resume"] or code is not None:
            self._send_to_client(control_response(request["id"], request["params"]["name"], self._proxy_status(), code=code))
        if self._subscriptions_changed.is_set():
            self._recovery_trigger.set()

    def _prepare_lifecycle(self):
        pending = self._pending_lifecycle
        request = pending["request"]
        tool = request["params"]["name"]
        code = self._assess_startup()
        prepared = None
        try:
            if code is None:
                self._check_proxy_drift()
                runtime = self._runtime_status()
                replacement = tool == RESTART_TOOL and (self._proxy_drift or runtime["restart_required"])
                if replacement:
                    state = pending["state"]
                    if runtime["required"] is None:
                        code = "runtime_installation_unavailable"
                    elif state is None or self._wake_write is None or os.name != "posix":
                        code = "proxy_handoff_unsupported"
                    elif self._public_session is None:
                        code = "not_initialised"
                    else:
                        state["python"] = runtime["required"]
                        image_hash = self._compute_proxy_hash()
                        with state_descriptor(state) as fd:
                            if self._preflight_handoff(fd, state["python"]):
                                prepared = {"state": state, "image_hash": image_hash}
                            else:
                                code = "proxy_handoff_preflight_failed"
                elif runtime["restart_required"]:
                    code = self._runtime_error()
                else:
                    child = self._get_child()
                    if self._client_protocol == "modern" and not self._initial_protocol_selected.is_set() and child is not None:
                        if not self._establish_initial_protocol(child):
                            code = "server_refresh_blocked"
                    if code is None:
                        child = self._get_child()
                        if (child is None or child.poll() is not None or self._interface_header is None
                                or self._last_launched_version != _read_brain_version_from_disk(self.vault_root)):
                            code = self._refresh_child()
                        if self._interface_header is not None:
                            self._initial_protocol_selected.set()
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            _log().warning("lifecycle preparation refused: %s", type(exc).__name__)
            code = "server_refresh_blocked"
        self._complete_lifecycle(pending, code, prepared=prepared)

    def _finish_prepared_handoff(self, stdin):
        try:
            pending, prepared = self._lifecycle_completion.get_nowait()
        except queue.Empty:
            return
        state = prepared["state"]
        code = self._assess_startup()
        if pending["cancelled"]:
            code = "request_cancelled"
        current = self._transport_state(pending["request"]["id"], stdin)
        if any(current[key] != state[key] for key in ("vault", "workspace", "protocol", "public_session", "generation", "subscriptions", "tools")):
            code = "session_changed"
        try:
            if (self._compute_proxy_hash() != prepared["image_hash"] or
                    not same_executable_path(state["python"], self._required_runtime_python())):
                code = "installation_changed"
            if code is None:
                state["remainder"] = current["remainder"]
                with state_descriptor(state) as fd:
                    read_state(fd, expected_pid=os.getpid())
                    with self._restart_lock:
                        self._restart_in_progress = False
                        self._pending_lifecycle = None
                    code = self._replace_idle_image(fd, state, image_hash=prepared["image_hash"])
        except (OSError, ValueError, subprocess.SubprocessError):
            if self._shutdown:
                raise
            code = "proxy_handoff_preflight_failed"
        if self._handoff_state is None and not self._shutdown:
            self._complete_lifecycle(pending, code)

    def _refresh_child(self) -> str | None:
        """Validate a candidate before retiring the idle previous child."""
        with self._interface_lock:
            previous_header = self._interface_header
            previous_error = self._interface_header_error
        installed = _read_brain_version_from_disk(self.vault_root)
        code = "server_refresh_blocked"
        try:
            if self._start_child():
                self._gave_up = False
                self._backoff_slot = 0
                self._refresh_blocked_version = None
                self._refresh_diagnostic = None
                self._refresh_header_rejected = False
                code = None
            else:
                with self._interface_lock:
                    self._refresh_header_rejected = self._interface_header_error is not None
                    self._refresh_diagnostic = (self._interface_header_error or "Replacement did not finish a stable installation handshake; repair installed files and request refresh again.")[:240]
                    self._interface_header = previous_header
                    self._interface_header_error = previous_error
                self._refresh_blocked_version = installed
            _op_event("child.refresh", outcome=code or "ok")
            return code
        finally:
            self._preparing_child = None

    def _maybe_begin_version_reset(self) -> bool:
        """
        After give-up, check whether VERSION changed and restart recovery if so.
        """
        with self._restart_lock:
            requested = self._version_reset_requested
            gave_up = self._gave_up
        if not requested or not gave_up:
            return False

        now = time.monotonic()
        if now - self._last_version_check < _get_version_check_interval():
            with self._restart_lock:
                self._version_reset_requested = False
            return False
        self._last_version_check = now

        current_version = _read_brain_version_from_disk(self.vault_root)
        if current_version == self._last_launched_version:
            with self._restart_lock:
                self._version_reset_requested = False
            return False

        _log().info(
            "brain-core VERSION changed (%s → %s) — resetting backoff",
            self._last_launched_version, current_version,
        )
        with self._restart_lock:
            self._gave_up = False
            self._backoff_slot = 0
            self._restart_in_progress = True
            self._recovery_exit_code = 1
            self._version_reset_requested = False
        return True

    def _recover_from_exit(self, exit_code: int) -> None:
        """
        Recovery-thread owner for restart/backoff.

        Detection paths only populate recovery state and wake this loop.
        """
        _log().info("child exited with code %d", exit_code)
        _op_event("child.exited", exit_code=exit_code)

        if exit_code == _EXIT_CODE_CLEAN:
            _log().info("clean child exit — proxy shutting down")
            self._fail_pending_replay("server shutting down")
            self._finish_recovery_cycle()
            self._initiate_shutdown()
            return

        if self._should_reset_backoff():
            _log().info("child was healthy for >%ds — resetting backoff", _CHILD_ALIVE_RESET_SECS)
            with self._restart_lock:
                self._backoff_slot = 0

        if exit_code == _EXIT_CODE_VERSION_DRIFT:
            _log().info("exit code %d: version drift — restarting immediately", exit_code)
            if self._start_child():
                self._replay_pending_requests()
                self._finish_recovery_cycle()
                return
            _log().warning("version-drift restart failed — falling through to backoff")
            with self._restart_lock:
                self._backoff_slot = 0

        while not self._shutdown:
            with self._restart_lock:
                if self._backoff_slot >= len(self._backoff_schedule):
                    attempts = len(self._backoff_schedule)
                    self._gave_up = True
                    self._restart_in_progress = False
                    self._recovery_exit_code = None
                    exhausted = True
                else:
                    slot = self._backoff_slot
                    delay = self._backoff_schedule[slot]
                    exhausted = False

            if exhausted:
                _log().error("backoff exhausted after %d attempts — giving up", attempts)
                self._fail_pending_replay("server restart failed")
                return

            _log().warning(
                "child restart — backoff slot %d/%d, waiting %ds",
                slot, len(self._backoff_schedule) - 1, delay,
            )
            _op_event("child.restart_scheduled", backoff_slot=slot, delay_s=delay)
            if delay > 0 and self._recovery_trigger.wait(timeout=delay):
                self._recovery_trigger.clear()
                if self._shutdown:
                    self._fail_pending_replay("server shutting down")
                    self._finish_recovery_cycle()
                    return

            with self._restart_lock:
                self._backoff_slot += 1
                attempt = self._backoff_slot
            if self._start_child():
                self._replay_pending_requests()
                self._finish_recovery_cycle()
                return
            _log().warning("child restart attempt %d failed", attempt)

        self._fail_pending_replay("server shutting down")
        self._finish_recovery_cycle()

    def _handle_recovery_thread_crash(self) -> None:
        """Mark the recovery thread as crashed and fail any saved replay work."""
        _log().exception("proxy recovery thread crashed")
        self._fail_pending_replay(_RECOVERY_THREAD_CRASHED_MSG)
        with self._restart_lock:
            self._restart_in_progress = False
            self._recovery_exit_code = None
            self._version_reset_requested = False
        self._recovery_thread_failed = True
        if self._preparing_child is not None:
            self._preparing_child.kill()
            self._preparing_child = None
        if self._pending_lifecycle is not None:
            self._complete_lifecycle(self._pending_lifecycle, "proxy_restart_required")

    def _run_recovery_thread(self) -> None:
        """Thread entrypoint wrapper so recovery crashes stay observable."""
        try:
            self._recovery_thread()
        except Exception:
            self._handle_recovery_thread_crash()

    def _recovery_thread(self) -> None:
        """Dedicated recovery loop. Owns every backoff sleep and restart attempt."""
        while True:
            if self._shutdown:
                return

            self._recovery_trigger.wait()
            self._recovery_trigger.clear()

            if self._shutdown:
                return

            if self._pending_lifecycle is not None:
                if not self._pending_lifecycle.get("prepared"):
                    self._prepare_lifecycle()
                continue

            if self._subscriptions_changed.is_set():
                self._subscriptions_changed.clear()
                # This worker owns replacement. Do not hold the publication
                # gate across pipe writes: the reader must drain child stdout.
                child = self._get_child()
                try:
                    if child is not None and child.poll() is None and not self._initial_protocol_selected.is_set():
                        self._establish_initial_protocol(child)
                    else:
                        self._sync_subscription_child(child)
                except OSError:
                    self._signal_recovery(child.poll(), child=child)

            self._maybe_begin_version_reset()

            with self._restart_lock:
                if self._pending_lifecycle is not None:
                    continue
                if not self._restart_in_progress:
                    continue
                exit_code = self._recovery_exit_code if self._recovery_exit_code is not None else 1

            self._recover_from_exit(exit_code)

    # ------------------------------------------------------------------
    # Reader thread (child stdout → proxy stdout)
    # ------------------------------------------------------------------

    def _drain_inflight(
        self,
    ) -> tuple[list[dict], dict[int | str, AcceptedCallRecord]]:
        """
        Atomically clear and return the in-flight request map.

        Caller decides what to do with the orphans (error to client, save for
        replay, etc.) — this method only owns the lock-protected snapshot.
        """
        with self._inflight_lock:
            orphans = [req for req, _ in self._inflight_requests.values()]
            accepted = {
                request_id: self._accepted_calls.pop(request_id)
                for request_id in tuple(self._accepted_calls)
                if request_id in self._inflight_requests
            }
            self._inflight_requests.clear()
            self._frame_seqs.clear()
        return orphans, accepted

    def _send_client_errors(self, requests: list[dict], message: str) -> None:
        """Send JSON-RPC error responses to the client for a list of requests."""
        for req in requests:
            req_id = req.get("id")
            _log().warning("orphaned in-flight request id=%s — sending error to client", req_id)
            self._send_to_client(_make_error_response(req_id, -32603, message))

    def _refuse_interface_replay(
        self,
        request_id: int | str | None,
        reason: str | None,
        *,
        detail: str | None = None,
    ) -> None:
        """Fail one replay without child dispatch and force tool re-discovery."""

        resolved_reason = reason or "indeterminate_interface_change"
        _log().warning(
            "refusing request replay id=%s reason=%s",
            request_id,
            resolved_reason,
        )
        _op_event("replay.refused", reason=resolved_reason)
        self._send_to_client(
            _interface_changed_response(
                request_id,
                resolved_reason,
                detail=detail,
            )
        )
        if self._client_protocol != "modern":
            self._send_to_client(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                }
            )

    def _replay_requests(
        self,
        requests: list[dict],
        accepted_calls: dict[int | str, AcceptedCallRecord],
    ) -> None:
        """
        Replay saved requests to the current child after a version-drift restart.
        Called from the recovery thread after a successful restart.
        """
        child = self._get_child()
        if child is None:
            self._send_client_errors(requests, "server restart failed")
            self._replay_depth = 0
            return

        replayed_any = False
        for req in requests:
            req_id = req.get("id")
            accepted = accepted_calls.get(req_id)
            if accepted is None and req.get("method") == "tools/call":
                self._refuse_interface_replay(req_id, "accepted_call_missing")
                continue
            if accepted is not None and not isinstance(accepted, AcceptedCallRecord):
                self._refuse_interface_replay(req_id, "accepted_call_invalid")
                continue
            if accepted is not None:
                self._send_to_client(_outcome_unknown_response(accepted,
                                     diagnostic="semantic invocation replay is forbidden"))
                continue
            _log().info("replaying request id=%s to new child", req_id)
            with self._inflight_lock:
                self._inflight_requests[req_id] = (req, time.monotonic())
                frame_seq = next(self._frame_counter)
                self._frame_seqs[req_id] = frame_seq
            try:
                child.send(req)
                _op_event(
                    "frame.forwarded",
                    family="proxy-rpc",
                    frame_seq=frame_seq,
                    method=_operational_log.normalise_rpc_method(req.get("method")),
                    replayed=True,
                )
                replayed_any = True
            except Exception as e:
                with self._inflight_lock:
                    if self._frame_seqs.get(req_id) == frame_seq:
                        self._inflight_requests.pop(req_id, None)
                        self._accepted_calls.pop(req_id, None)
                        self._frame_seqs.pop(req_id, None)
                _log().error("replay failed for request id=%s: %s", req_id, e)
                self._send_client_errors([req], "replay failed after restart")
        if not replayed_any:
            self._replay_depth = 0

    def _reader_thread(self) -> None:
        """
        Continuously reads from child stdout and writes to proxy stdout.
        Uses select() with timeout for health checking. Handles child exit,
        version-drift replay, and restart logic.
        """
        timeout = _get_read_timeout()
        hang_counter = 0

        try:
            while not self._shutdown:
                if not self._initial_protocol_selected.is_set():
                    self._initial_protocol_selected.wait(timeout=1.0)
                    continue
                child = self._get_child()
                if child is None:
                    self._child_ready.wait(timeout=1.0)
                    self._child_ready.clear()
                    hang_counter = 0
                    continue

                if sys.platform == "win32":
                    # Windows select() cannot wait on anonymous pipe handles.
                    # This reader is already a daemon thread, so block directly
                    # and rely on EOF/crash recovery; timeout-based hang
                    # detection remains available on Unix.
                    line = child.readline()
                else:
                    fd = child.stdout_fd
                    if fd is None:
                        line = None
                    else:
                        line = None
                        ready = None
                        try:
                            ready = [fd] if child.line_ready else select.select([fd], [], [], timeout)[0]
                        except (ValueError, OSError):
                            line = None
                        if ready is not None:
                            if ready:
                                line = True
                                hang_counter = 0
                            else:
                                # Timeout — check child health
                                poll = child.poll()
                                if poll is not None:
                                    # Child already dead — treat as EOF
                                    line = None
                                else:
                                    # Child alive — check for hang
                                    with self._inflight_lock:
                                        has_inflight = bool(self._inflight_requests)
                                    if has_inflight:
                                        hang_counter += 1
                                        _log().warning(
                                            "child unresponsive with in-flight requests "
                                            "(timeout %d/%d)",
                                            hang_counter, _HANG_CONSECUTIVE_LIMIT,
                                        )
                                        if hang_counter >= _HANG_CONSECUTIVE_LIMIT:
                                            _log().error(
                                                "killing hung child after %d consecutive timeouts",
                                                hang_counter,
                                            )
                                            child.kill()
                                            line = None  # trigger EOF path
                                        else:
                                            continue
                                    else:
                                        # No in-flight requests — idle is fine
                                        continue

                with self._publication_gate:
                    if self._shutdown:
                        break
                    if child is not self._get_child():
                        continue
                    if line is True:
                        line = child.readline()
                    if line is None:
                        # Child stdout closed — wait for exit code
                        exit_code = child.wait()
                        _log().info("child stdout EOF, exit code=%s", exit_code)
                        hang_counter = 0
                        self._signal_recovery(exit_code, child=child)
                        continue

                    # Parse and forward to client
                    try:
                        obj = json.loads(line.decode("utf-8").strip())
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        _log().debug("failed to parse child output: %s", e)
                        continue

                    subscription = child.subscription_id
                    metadata = obj.get("params", {}).get("_meta", {})
                    stream_id = metadata.get(SUBSCRIPTION_ID)
                    if obj.get("method") == "notifications/subscriptions/acknowledged" and subscription is not None and stream_id == subscription:
                        # Listen registration, not pipe-send completion, is the
                        # coverage barrier. Level-trigger a refetch to cover
                        # changes during asynchronous bridge reconfiguration.
                        self._subscriptions.invalidate(self._send_to_client)
                        continue
                    ended_stream = (obj.get("id") == subscription and not obj.get("method") or
                                    obj.get("method") == "notifications/cancelled" and obj.get("params", {}).get("requestId") == subscription)
                    if subscription is not None and ended_stream:
                        child.subscription_id = None
                        self._subscriptions.end("child subscription ended; re-listen and refetch", self._send_to_client)
                        continue
                    if obj.get("method") in EVENT_FILTERS and self._client_protocol == "modern":
                        if subscription is not None and stream_id == subscription:
                            self._subscriptions.emit(obj, self._send_to_client)
                        continue
                    if obj.get("method") == "notifications/subscriptions/acknowledged" or (
                            isinstance(obj.get("id"), str) and obj["id"].startswith("brain-proxy-subscription-")):
                        continue

                    if obj.get("method") == "notifications/cancelled":
                        obj = self._translate_child_cancellation(child, obj)
                        if obj is None:
                            continue
                    msg_id = obj.get("id")
                    method = obj.get("method")

                    # Remove from in-flight tracking (response received) and
                    # compute round-trip latency so we can attribute slow calls
                    # to the child rather than the proxy.
                    latency_s: float | None = None
                    frame_seq: int | None = None
                    if msg_id is not None and method:
                        obj = self._correlate_child_request(child, obj)
                    if msg_id is not None and not method:
                        with self._inflight_lock:
                            entry = self._inflight_requests.pop(msg_id, None)
                            if entry is not None:
                                self._record_tool_discovery(obj, entry[0])
                            self._accepted_calls.pop(msg_id, None)
                            frame_seq = self._frame_seqs.pop(msg_id, None)
                        if entry is not None:
                            request, sent_at = entry
                            obj = add_control_discovery(obj, request)
                            latency_s = time.monotonic() - sent_at
                        self._replay_depth = 0

                    if latency_s is not None:
                        _log().debug(
                            "child→client: id=%s method=%s latency=%.3fs",
                            msg_id, method, latency_s,
                        )
                    else:
                        _log().debug("child→client: id=%s method=%s", msg_id, method)
                    if frame_seq is not None:
                        _op_event(
                            "frame.completed",
                            family="proxy-rpc",
                            frame_seq=frame_seq,
                            duration_ms=(
                                None if latency_s is None else int(latency_s * 1000)
                            ),
                            outcome="error" if "error" in obj else "ok",
                        )
                    if _LOG_BODIES:
                        _capture_body("child_to_client", method, line)

                    # Capture initialize response (first time only)
                    if (
                        self._init_response is None
                        and self._init_request is not None
                        and msg_id == self._init_request.get("id")
                        and "result" in obj
                    ):
                        self._init_response = obj
                        self._public_session = public_session(obj)
                        self._capture_interface_header(obj)
                        _log().info("captured initialize response from child")

                    self._send_to_client(obj)
        except Exception:
            _log().exception("proxy reader thread crashed")
            self._initiate_shutdown()

    def _correlate_child_request(self, child: ChildProcess, request: dict) -> dict:
        host_id = f"brain-proxy-host-{uuid.uuid4()}"
        with self._inflight_lock:
            self._server_requests[host_id] = (child, request["id"])
        return {**request, "id": host_id}

    def _translate_child_cancellation(self, child: ChildProcess, notification: dict) -> dict | None:
        params = notification.get("params", {})
        original_id = params.get("requestId")
        with self._inflight_lock:
            for host_id, pending in tuple(self._server_requests.items()):
                if pending == (child, original_id):
                    del self._server_requests[host_id]
                    return {**notification, "params": {**params, "requestId": host_id}}
        return None

    def _forward_host_response(self, response: dict) -> None:
        with self._inflight_lock:
            pending = self._server_requests.pop(response["id"], None)
        if pending is None:
            return
        child, original_id = pending
        if child is not self._get_child() or child.poll() is not None:
            return
        try:
            child.send({**response, "id": original_id})
        except OSError:
            self._signal_recovery(child.poll(), child=child)

    def _get_child(self) -> ChildProcess | None:
        with self._child_lock:
            return self._child

    def _record_tool_discovery(self, response: dict, request: dict) -> None:
        """Record exposed contracts while in-flight ownership still bars refresh."""
        if request.get("method") != "tools/list" or not isinstance(response.get("result"), dict):
            return
        with self._interface_lock:
            if self._interface_header is None or self._advertised_tools is None:
                return
            for tool in response["result"].get("tools", []):
                name = tool.get("name") if isinstance(tool, dict) else None
                mapping = self._interface_header.tool(name)
                if mapping is not None:
                    self._advertised_tools[name] = mapping

    # ------------------------------------------------------------------
    # Main loop (proxy stdin → child stdin)
    # ------------------------------------------------------------------

    def _error_response_for_dead_child(self, msg_id: int | str | None) -> dict:
        if self._recovery_thread_failed:
            message = _RECOVERY_THREAD_CRASHED_MSG
        elif self._gave_up:
            message = _unrecoverable_msg(
                f"server restart failed after {len(self._backoff_schedule)} recovery attempts"
            )
        else:
            message = "server restarting, please retry"
        return _make_error_response(msg_id, -32603, message)

    def _prepare_interface_call(
        self,
        request: dict,
    ) -> tuple[dict, AcceptedCallRecord | None]:
        """Bind granular calls when the child advertised their exact contract."""

        with self._interface_lock:
            header = self._interface_header
        if header is None:
            raise ValueError("the child has no validated Brain command interface")
        params = request.get("params")
        tool_name = params.get("name") if isinstance(params, dict) else None
        if not isinstance(tool_name, str):
            raise ValueError("tools/call requires a string tool name")
        if header.tool(tool_name) is None:
            raise ValueError(
                "tool is not advertised by the active Brain command interface; "
                "re-discover tools and use the canonical granular command"
            )
        if self._advertised_tools is not None and self._advertised_tools.get(tool_name) != header.tool(tool_name):
            raise ValueError("the tool contract changed; re-discover tools before making a new request")
        invocation_id = f"mcp-{uuid.uuid4()}"
        record, forwarded = accept_call(
            request,
            header,
            invocation_id=invocation_id,
            accepted_at=datetime.now(timezone.utc),
        )
        return forwarded, record

    def _serve_transport_request(self, obj: dict) -> bool:
        method, params = obj.get("method"), obj.get("params", {})
        if not isinstance(params, dict):
            self._send_to_client(_make_error_response(obj.get("id"), -32602, "params must be an object"))
            return True
        modern = _uses_modern_protocol(obj)
        if modern:
            negotiated = discovery(obj)
            if self._client_protocol == "legacy":
                raise ValueError("MCP protocol era cannot change within a session")
        elif method == "initialize" and self._client_protocol == "modern":
            raise ValueError("MCP protocol era cannot change within a session")
        if modern and self._client_protocol is None:
            self._client_protocol = "modern"
            self._public_session = public_session(negotiated)
        if method == "notifications/cancelled":
            with self._restart_lock:
                pending = self._pending_lifecycle
                if pending is not None and params.get("requestId") == pending["request"]["id"]:
                    if not pending.get("activated"):
                        pending["cancelled"] = True
                        if self._preparing_child is not None:
                            self._preparing_child.kill()
                    return True
            if self._subscriptions.cancel(params.get("requestId")):
                self._request_subscription_sync()
                return True
        if method == "subscriptions/listen" and modern:
            self._subscriptions.open(obj.get("id"), params.get("notifications"), self._send_to_client)
            self._request_subscription_sync()
            return True
        if method == "ping":
            if "id" in obj:
                self._send_to_client({"jsonrpc": "2.0", "id": obj["id"], "result": {}})
            return True
        if self._get_child() is not None:
            return False
        if method in ("initialize", "server/discover"):
            response = discovery(obj)
            self._client_protocol = "modern" if modern else "legacy"
            if not modern:
                self._init_request, self._init_response = obj, response
            self._public_session = public_session(response)
            self._advertised_tools = {}
            if self._startup_failure:
                response["result"]["instructions"] = self._startup_failure.detail
            self._send_to_client(response)
            return True
        if method in ("resources/list", "resources/templates/list", "prompts/list"):
            key = {"resources/list": "resources", "resources/templates/list": "resourceTemplates", "prompts/list": "prompts"}[method]
            self._send_to_client({"jsonrpc": "2.0", "id": obj.get("id"), "result": {key: []}})
            return True
        return False

    def run(self, *, remainder: bytes = b"", stdin_stream=None, stdout_stream=None) -> tuple[dict, str] | None:
        """Main proxy loop. Reads from stdin, forwards to child."""
        self._output_stream = stdout_stream
        source = stdin_stream if stdin_stream is not None else sys.stdin.buffer
        try:
            stdin = RawLineReader(source.fileno(), remainder) if sys.platform != "win32" else source
        except (AttributeError, OSError):
            stdin = source
        if isinstance(stdin, RawLineReader):
            self._wake_read, self._wake_write = os.pipe()
            os.set_blocking(self._wake_write, False)
        self._ensure_background_threads()

        while not self._shutdown:
            try:
                line = stdin.readline_interruptible(self._wake_read) if self._wake_read is not None else stdin.readline()
                if line is None:
                    if not self._shutdown:
                        self._finish_prepared_handoff(stdin)
                    if self._handoff_state is not None:
                        for fd in (self._wake_read, self._wake_write):
                            os.close(fd)
                        self._wake_read = self._wake_write = None
                        return self._handoff_state, self._handoff_error
                    continue
            except Exception as e:
                _log().error("error reading from stdin: %s", e)
                self._initiate_shutdown()
                break

            if not line:
                _log().info("stdin EOF — proxy shutting down")
                self._initiate_shutdown()
                break

            line = line.strip()
            if not line:
                continue

            obj = _parse_jsonrpc_line(line, log_warning=True)
            if obj is None:
                continue

            has_request_id = "id" in obj
            msg_id = obj.get("id")
            if has_request_id and not _is_jsonrpc_request_id(msg_id):
                _log().warning("rejected JSON-RPC frame with an invalid request id")
                self._send_to_client(
                    _make_error_response(
                        None,
                        -32600,
                        "Invalid Request: JSON-RPC id must be a string or integer",
                    )
                )
                continue

            method = obj.get("method", "")
            is_notification = not has_request_id and bool(method)
            is_request = has_request_id and bool(method)
            if has_request_id and not method:
                self._forward_host_response(obj)
                continue

            if method == "tools/call" and not is_request:
                _log().warning("rejected tools/call notification without a request id")
                continue

            if self.server_target == "brain_mcp.server":
                try:
                    if self._serve_transport_request(obj):
                        continue
                except (ValueError, TypeError) as exc:
                    if is_request:
                        self._send_to_client(_make_error_response(msg_id, -32602, str(exc)))
                    continue

            params = obj.get("params")
            tool_name = params.get("name") if isinstance(params, dict) else None
            if method == "tools/call" and tool_name in CONTROL_TOOLS:
                if self._client_protocol is None:
                    self._client_protocol = "modern" if _uses_modern_protocol(obj) else "legacy"
                arguments = params.get("arguments", {})
                code = None
                if not isinstance(arguments, dict) or arguments:
                    code = "invalid_arguments"
                elif tool_name in (RESTART_TOOL, REFRESH_TOOL):
                    code = self._admit_lifecycle(obj, stdin)
                    if code is None:
                        continue
                self._send_to_client(control_response(msg_id, tool_name, self._proxy_status(), code=code))
                continue

            if self._pending_lifecycle is not None:
                if is_request:
                    self._send_to_client(_make_error_response(msg_id, -32001, "Brain lifecycle recovery in progress; retry after completion"))
                continue

            if method == "tools/call":
                if self._startup_failure is not None and self._get_child() is None:
                    self._send_to_client(_make_error_response(msg_id, -32001, self._startup_failure.detail))
                    continue
                runtime_error = self._runtime_error()
                if runtime_error is not None:
                    self._send_to_client(control_response(msg_id, tool_name, self._proxy_status(), code=runtime_error))
                    continue

            _log().debug("client→child: id=%s method=%s", msg_id, method)
            if _LOG_BODIES:
                _capture_body("client_to_child", method, line)

            # Keep the latest initialize request until we capture a matching
            # initialize response from a live child.
            if method == "initialize" and self._init_response is None:
                self._init_request = obj
                _log().info("captured initialize request from client")

            child = self._get_child()
            if child is not None:
                exit_code = child.poll()
                if exit_code is not None:
                    self._signal_recovery(exit_code, child=child)
                    child = None

            recovery_thread_dead = child is None and self._recovery_thread_is_dead()
            if recovery_thread_dead and (
                self._pending_replay
                or self._pending_unexpected
                or not self._recovery_thread_failed
            ):
                self._recovery_thread_failed = True
                self._fail_pending_replay(_RECOVERY_THREAD_CRASHED_MSG)

            if self._restart_in_progress or child is None:
                if self._gave_up and method == "tools/call" and not recovery_thread_dead:
                    self._signal_version_reset()
                if is_request and method == "tools/list":
                    tools = tool_definitions()
                    self._send_to_client({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})
                elif is_request:
                    self._send_to_client(_make_error_response(msg_id, -32001, self._startup_failure.detail)
                                         if self._startup_failure else self._error_response_for_dead_child(msg_id))
                elif is_notification:
                    _log().debug("dropping notification (child dead): method=%s", method)
                continue

            if not self._initial_protocol_selected.is_set():
                if self._client_protocol is None:
                    self._client_protocol = "modern" if _uses_modern_protocol(obj) else "legacy"
                # The SDK locks a stdio connection to its first protocol era.
                # Modern discovery is proxy-owned; legacy initialize stays
                # host-owned so its capabilities and instructions are exact.
                ready = self._establish_initial_protocol(child)
                if not ready:
                    if is_request:
                        self._send_to_client(_interface_changed_response(msg_id, "child_header_invalid", detail=self._interface_header_error))
                    continue

            accepted_call = None
            if method == "tools/call":
                installed = _read_brain_version_from_disk(self.vault_root)
                if installed != self._last_launched_version:
                    code = ("server_refresh_blocked" if self._refresh_blocked_version == installed
                            else self._admit_lifecycle(obj, stdin, resume=True))
                    if code is not None:
                        self._send_to_client(control_response(msg_id, REFRESH_TOOL, self._proxy_status(), code=code))
                    continue
                try:
                    runtime_error = self._runtime_error()
                    if runtime_error is not None:
                        self._send_to_client(control_response(msg_id, tool_name, self._proxy_status(), code=runtime_error))
                        continue
                    obj, accepted_call = self._prepare_interface_call(obj)
                except (TypeError, ValueError) as exc:
                    self._refuse_interface_replay(
                        msg_id,
                        "accepted_call_invalid",
                        detail=str(exc),
                    )
                    continue

            self._forward_to_child(obj, child, accepted_call)

        # Shutdown — kill child if still running
        self._initiate_shutdown()
        child = self._get_child()
        if child:
            _log().info("proxy shutting down — killing child pid=%s", child.pid)
            child.kill()
        for thread in (self._reader_thread_handle, self._recovery_thread_handle):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
        self._outbound.put(None)
        writer = self._writer_thread_handle
        if writer is not None and writer.is_alive():
            writer.join(timeout=5.0)
        for fd in (self._wake_read, self._wake_write):
            if fd is not None:
                os.close(fd)
        self._wake_read = self._wake_write = None
        logger = _operational_log.current_logger()
        if logger is not None:
            logger.close(exit_code=0)
        _log().info("proxy exited")

    def _forward_to_child(self, obj, child, accepted_call):
        """Admit a request exactly once, including a held pre-dispatch refresh."""
        msg_id, method = obj.get("id"), obj.get("method")
        is_request = "id" in obj and bool(method)
        if child is None:
            if is_request:
                self._send_to_client(self._error_response_for_dead_child(msg_id))
            return
        frame_seq: int | None = None
        if is_request:
            with self._inflight_lock:
                self._inflight_requests[msg_id] = (obj, time.monotonic())
                if accepted_call is not None:
                    self._accepted_calls[msg_id] = accepted_call
                frame_seq = next(self._frame_counter)
                self._frame_seqs[msg_id] = frame_seq
        try:
            child.send(obj)
        except BrokenPipeError:
            _log().warning("child stdin broken pipe — child likely died")
            # poll() can return None if reaping hasn't completed; wait()
            # gives the real exit code so a drift exit (10) isn't
            # misclassified as a crash and silently loses replay.
            exit_code = child.poll()
            if exit_code is None:
                try:
                    exit_code = child.wait()
                except Exception:
                    exit_code = 1
            self._recover_owed_client(msg_id, child, exit_code, is_request)
            return
        except Exception as e:
            _log().error("error sending to child (%s): %s", type(e).__name__, e)
            exit_code = child.poll()
            if exit_code is None:
                exit_code = 1
            self._recover_owed_client(msg_id, child, exit_code, is_request)
            return

        if frame_seq is not None:
            _op_event(
                "frame.forwarded",
                family="proxy-rpc",
                frame_seq=frame_seq,
                method=_operational_log.normalise_rpc_method(method),
            )



# ---------------------------------------------------------------------------
# Degraded mode (startup failure)
# ---------------------------------------------------------------------------

def _run_degraded_server(
    reason: str,
    *,
    stdin=None,
    stdout=None,
    guidance: str = _GUIDANCE_BINDING,
    lead: str = "Brain MCP could not resolve a target vault.",
    vault_root: str | None = None,
    python_path: str | None = None,
    resolution_inputs: dict | None = None,
) -> None:
    """Serve a minimal MCP session that reports a startup-degraded Brain error.

    The proxy reaches here when startup cannot continue before a real child MCP
    server exists: unresolved Brain binding/default failures, filesystem probe
    failures during resolution, or resolved-vault failures such as an
    inaccessible proxy log. Exiting instead would make the client report a
    generic "-32000 failed to reconnect" and bury the actionable cause on
    stderr. So we complete the MCP handshake and surface the startup error to
    the agent via ``brain_unavailable`` and every ``tools/call`` response.
    """
    detail = f"{lead} {reason} {guidance}"
    _log().error("entering degraded mode: %s", reason)
    failure = StartupFailure("startup", "startup_unavailable" if vault_root else "target_unavailable", detail)
    if stdin is not None or stdout is not None:
        relay = Proxy(python_path or sys.executable, "brain_mcp.server", vault_root,
                      startup_failure=failure, resolution_inputs=resolution_inputs)
        relay.run(stdin_stream=stdin, stdout_stream=stdout)
    else:
        _serve_proxy(python_path or sys.executable, "brain_mcp.server", vault_root,
                     startup_failure=failure, resolution_inputs=resolution_inputs)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _create_process_owner(vault_root: str, *, lock_timeout: float | None = None) -> tuple[ConsentOwner | None, str | None]:
    """Degrade only initial owner setup; established connection loss never falls back."""
    if os.name != "posix":
        return None, "platform"
    try:
        return ConsentOwner(Path(vault_root), lock_timeout=lock_timeout), None
    except (OSError, MutationLockError):
        _log().warning("private consent state unavailable; ordinary command startup continues")
        return None, "storage"


def _serve_proxy(python_path: str, server_target: str, vault_root: str | None, *, state: dict | None = None,
                 startup_failure: StartupFailure | None = None, resolution_inputs: dict | None = None) -> None:
    failure = None
    loaded_hash = None
    while True:
        owner, unavailable = _create_process_owner(vault_root, lock_timeout=HANDOFF_TIMEOUT if state else None) if vault_root else (None, "storage")
        proxy = Proxy(python_path, server_target, vault_root, owner=owner, owner_unavailable_code=unavailable,
                      startup_failure=startup_failure, resolution_inputs=resolution_inputs)
        if loaded_hash is None:
            loaded_hash = proxy._proxy_file_hash
        else:
            proxy._proxy_file_hash = loaded_hash
        try:
            if state is not None:
                proxy._restore_transport(state)
                startup_failure = None
            proxy._ensure_background_threads()
            ready = proxy._start_child() if startup_failure is None else False
            if state is not None:
                proxy._initial_protocol_selected.set()
                status = proxy._proxy_status()
                status["handoff"] = {"consent": "fresh" if owner else "unavailable", "state": "failed" if failure or not ready else "completed"}
                proxy._send_to_client(control_response(state["request_id"], RESTART_TOOL, status,
                                                       code=failure or (None if ready else "proxy_server_start_failed"), effects="consent_ended"))
            if not ready and startup_failure is None:
                proxy._signal_recovery(exit_code=1)
            next_state = proxy.run(remainder=base64.b64decode(state["remainder"]) if state else b"")
        finally:
            proxy._initiate_shutdown()
        if next_state is None:
            return
        state, failure = next_state


def _handoff_entry(mode: str, fd: int) -> None:
    probe = mode == "--check-handoff"
    state = read_state(fd, expected_pid=os.getppid() if probe else os.getpid())
    if not probe:
        os.close(fd)
    vault = state["vault"]
    installed_proxy = Path(vault) / ".brain-core" / "brain_mcp" / "proxy.py"
    if Path(__file__).resolve() != installed_proxy.resolve() or not same_executable_path(sys.executable, state["python"]):
        raise ValueError("handoff image or runtime does not match the pinned Brain")
    os.environ["BRAIN_VAULT_ROOT"] = vault
    os.environ["PYTHONPATH"] = str(Path(vault) / ".brain-core")
    if state["workspace"] is None:
        os.environ.pop("BRAIN_WORKSPACE_DIR", None)
    else:
        os.environ["BRAIN_WORKSPACE_DIR"] = state["workspace"]
    if probe:
        candidate = Proxy(state["python"], state["server"], vault)
        candidate._restore_transport(state)
        try:
            if not candidate._start_child():
                raise ValueError("replacement cannot preserve the public MCP session")
        finally:
            child = candidate._get_child()
            if child is not None:
                child.kill()
        return
    global _logger
    _logger = _setup_logging(vault)
    try:
        _operational_log.install(Path(vault), "proxy")
    except Exception as exc:
        _log().warning("operational diagnostics unavailable: %s", exc)
    _serve_proxy(state["python"], state["server"], vault, state=state)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] in {"--check-handoff", "--handoff-fd"}:
        _handoff_entry(sys.argv[1], int(sys.argv[2]))
        return
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} <python_path> <server_target>",
            file=sys.stderr,
        )
        sys.exit(1)

    python_path = sys.argv[1]
    server_target = sys.argv[2]

    # Startup resolves only. Legacy writes belong to admitted migration.
    workspace_env = os.environ.get("BRAIN_WORKSPACE_DIR")
    vault_root_env = os.environ.get("BRAIN_VAULT_ROOT")
    resolution_inputs = {"workspace_env": workspace_env, "vault_root_env": vault_root_env, "start_dir": Path.cwd()}

    try:
        target = resolve_brain_target(
            workspace_env=workspace_env,
            vault_root_env=vault_root_env,
            start_dir=Path.cwd(),
        )
    except WorkspaceBindingError as exc:
        # Resolution failed before any MCP session exists. Don't exit (the client
        # would surface a generic "-32000 failed to reconnect" and lose the cause)
        # — run a degraded server that completes the handshake and delivers the
        # actionable resolution error to the agent.
        print(f"brain-proxy: {exc}", file=sys.stderr)
        _run_degraded_server(str(exc), guidance=_restart_guidance_for_binding_error(exc))
        return
    except OSError as exc:
        # Raw filesystem errors can still escape target resolution before the
        # MCP session exists. Keep the handshake alive, but use
        # permission/storage-specific guidance instead of binding/default wording.
        print(f"brain-proxy: filesystem access failed while resolving Brain target: {exc}", file=sys.stderr)
        _run_degraded_server(
            f"filesystem access failed while resolving Brain target: {exc}",
            guidance=_GUIDANCE_FILESYSTEM,
            lead="Brain MCP could not inspect the filesystem while resolving a target vault.",
        )
        return

    vault_root = target.vault_root
    workspace_dir = target.workspace_dir

    os.environ["BRAIN_VAULT_ROOT"] = vault_root
    os.environ["PYTHONPATH"] = str(Path(vault_root) / ".brain-core")
    if workspace_dir is not None:
        os.environ["BRAIN_WORKSPACE_DIR"] = workspace_dir
    else:
        os.environ.pop("BRAIN_WORKSPACE_DIR", None)

    global _logger
    try:
        _probe_local_state(vault_root)
    except OSError as exc:
        print(
            f"brain-proxy: filesystem access failed while preparing vault-local state: {exc}",
            file=sys.stderr,
        )
        _run_degraded_server(
            f"filesystem access failed while preparing vault-local state for {vault_root}: {exc}",
            guidance=_GUIDANCE_VAULT_FILESYSTEM,
            lead="Brain MCP resolved the target vault but could not start.",
            vault_root=vault_root, python_path=python_path, resolution_inputs=resolution_inputs,
        )
        return
    _logger = _setup_logging(vault_root)

    # Operational diagnostics are best-effort by contract: a failure here
    # degrades to a no-op stream and never blocks serving.
    try:
        op_logger = _operational_log.install(Path(vault_root), "proxy")
    except Exception as exc:
        op_logger = None
        _log().warning("operational diagnostics unavailable: %s", exc)

    try:
        expected_python = _installed_runtime_python(vault_root)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if server_target == "brain_mcp.server":
            _run_degraded_server(
                str(exc),
                guidance=f"Run `{build_repair_command(vault_root, 'runtime')}` from a shell, then restart MCP.",
                lead="Brain MCP could not select an installed managed runtime.",
                vault_root=vault_root, python_path=python_path, resolution_inputs=resolution_inputs,
            )
            return
        _log().warning("could not resolve canonical managed Python for launch validation: %s", exc)
    else:
        if not same_executable_path(python_path, expected_python):
            message = (
                "Brain MCP launch config points at a non-canonical Python "
                f"({python_path}); expected the managed runtime ({expected_python})."
            )
            print(f"brain-proxy: {message}", file=sys.stderr)
            _run_degraded_server(
                message,
                guidance=f"Run `{build_repair_command(vault_root, 'mcp')}` from a shell, then restart MCP.",
                lead="Brain MCP resolved the target vault but found stale MCP registration state.",
                vault_root=vault_root, python_path=python_path, resolution_inputs=resolution_inputs,
            )
            return

    _log().info("proxy starting: version=%s source=%s", PROXY_VERSION, target.source)
    if op_logger is not None:
        op_logger.record("process.started", bodies_enabled=_LOG_BODIES or None, resolution_source=target.source)
    _serve_proxy(python_path, server_target, vault_root, resolution_inputs=resolution_inputs)


if __name__ == "__main__":
    main()
