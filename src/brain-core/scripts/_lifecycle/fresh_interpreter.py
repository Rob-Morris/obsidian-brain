"""Run one lifecycle owner in a fresh interpreter so its memory dies with it.

Long-lived adapters (the MCP server) must not host full-corpus embedding
work: onnxruntime arenas and NumPy buffers from a corpus encode are never
returned to the operating system, so an in-process rebuild leaves the server
several hundred megabytes heavier for the rest of its life. Delegating the
owner call to a child interpreter that exits afterwards bounds that cost to the
duration of the command.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
LIFECYCLE_TARGET_PREFIX = "_lifecycle."
FRESH_INTERPRETER_TIMEOUT_SECONDS = 1800

_CHILD_BOOTSTRAP = """
import io, json, sys
sys.path.insert(0, sys.argv[1])
from _lifecycle.fresh_interpreter import run_target_from_stdin
run_target_from_stdin()
"""


class FreshInterpreterError(RuntimeError):
    """The child interpreter did not return a lifecycle result."""


def run_lifecycle_in_fresh_interpreter(
    target: str,
    vault_root: str | Path,
    *,
    timeout: float = FRESH_INTERPRETER_TIMEOUT_SECONDS,
    **kwargs,
) -> dict:
    """Invoke ``_lifecycle.<module>:<function>(vault_root, **kwargs)`` in a child process.

    ``kwargs`` and the returned value must be JSON-serialisable. Exceptions
    raised by the target are re-raised here as ``FreshInterpreterError`` carrying
    the child's exception type and message.
    """
    _validate_target(target)
    request = {"target": target, "vault_root": str(vault_root), "kwargs": kwargs}
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _CHILD_BOOTSTRAP, str(SCRIPTS_DIR)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FreshInterpreterError(
            f"{target} did not finish within {timeout:g} seconds"
        ) from exc
    except OSError as exc:
        raise FreshInterpreterError(f"could not start a fresh interpreter for {target}: {exc}") from exc

    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise FreshInterpreterError(
            f"{target} returned no result (exit {completed.returncode}): {completed.stderr.strip()[-2000:]}"
        ) from exc
    if "error" in payload:
        error = payload["error"]
        raise FreshInterpreterError(f"{target} failed with {error['type']}: {error['message']}")
    return payload["result"]


def run_target_from_stdin() -> None:
    """Child entry point: read one request from stdin, write one JSON reply to stdout."""
    request = json.load(sys.stdin)
    reply_stream = sys.stdout
    # Owners may print progress; keep the reply channel clean.
    sys.stdout = sys.stderr
    try:
        function = _resolve_target(request["target"])
        result = function(request["vault_root"], **request["kwargs"])
        reply = {"result": result}
    except BaseException as exc:  # the parent re-raises this as a typed error
        reply = {"error": {"type": type(exc).__name__, "message": str(exc)}}
    reply_stream.write(json.dumps(reply))
    reply_stream.flush()


def _validate_target(target: str) -> None:
    module_name, separator, function_name = target.partition(":")
    if (
        separator != ":"
        or not module_name.startswith(LIFECYCLE_TARGET_PREFIX)
        or not function_name.isidentifier()
    ):
        raise ValueError(
            f"fresh-interpreter target must be '_lifecycle.<module>:<function>', got {target!r}"
        )


def _resolve_target(target: str):
    _validate_target(target)
    module_name, _, function_name = target.partition(":")
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, function_name)
