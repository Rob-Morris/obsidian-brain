"""Run one lifecycle owner in a fresh interpreter so its memory dies with it.

Long-lived adapters (the MCP server) must not host full-corpus embedding
work: onnxruntime arenas and NumPy buffers from a corpus encode are never
returned to the operating system, so an in-process rebuild leaves the server
several hundred megabytes heavier for the rest of its life. Delegating the
owner call to a child interpreter that exits afterwards bounds that cost to the
duration of the command.
"""

from __future__ import annotations

from collections.abc import Callable
import contextlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
LIFECYCLE_TARGET_PREFIX = "_lifecycle."
FRESH_INTERPRETER_TIMEOUT_SECONDS = 1800

_CHILD_BOOTSTRAP = """
import sys
sys.path.insert(0, sys.argv[1])
from _lifecycle.fresh_interpreter import run_target_from_stdin
run_target_from_stdin()
"""


class FreshInterpreterError(RuntimeError):
    """The child interpreter did not return a lifecycle result."""


def run_lifecycle_in_fresh_interpreter(
    owner: Callable[..., dict],
    vault_root: str | Path,
    *,
    timeout: float = FRESH_INTERPRETER_TIMEOUT_SECONDS,
    **kwargs,
) -> dict:
    """Invoke ``owner(vault_root, **kwargs)`` in a child process and return its result.

    ``owner`` must be a module-level function in the ``_lifecycle`` package;
    ``kwargs`` and the returned value must be JSON-serialisable. Exceptions
    raised by the owner are re-raised here as ``FreshInterpreterError``
    carrying the child's exception type and message.
    """
    target = _target_for(owner)
    request = {"target": target, "vault_root": str(vault_root), "kwargs": kwargs}
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _CHILD_BOOTSTRAP, str(SCRIPTS_DIR)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
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
    with reply_channel() as reply_stream:
        try:
            function = _resolve_target(request["target"])
            reply = {"result": function(request["vault_root"], **request["kwargs"])}
        except Exception as exc:  # the parent re-raises this as a typed error
            reply = {"error": {"type": type(exc).__name__, "message": str(exc)}}
        reply_stream.write(json.dumps(reply))


@contextlib.contextmanager
def reply_channel():
    """Yield a stream on the original stdout while everything else goes to stderr.

    Owners and the subprocesses they spawn (pip, native downloaders) write to
    file descriptor 1 directly, so swapping ``sys.stdout`` alone is not enough:
    the descriptor itself is pointed at stderr for the duration.
    """
    sys.stdout.flush()
    original_stdout_fd = os.dup(1)
    saved_stdout = sys.stdout
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        with os.fdopen(os.dup(original_stdout_fd), "w", encoding="utf-8") as reply_stream:
            yield reply_stream
    finally:
        sys.stdout = saved_stdout
        os.dup2(original_stdout_fd, 1)
        os.close(original_stdout_fd)


def _target_for(owner: Callable[..., dict]) -> str:
    target = f"{getattr(owner, '__module__', None)}:{getattr(owner, '__qualname__', None)}"
    _validate_target(target)
    return target


def _validate_target(target: str) -> None:
    module_name, separator, function_name = target.partition(":")
    if (
        separator != ":"
        or not module_name.startswith(LIFECYCLE_TARGET_PREFIX)
        or not function_name.isidentifier()
    ):
        raise ValueError(
            f"fresh-interpreter target must be a module-level function in {LIFECYCLE_TARGET_PREFIX}*, got {target!r}"
        )


def _resolve_target(target: str):
    _validate_target(target)
    module_name, _, function_name = target.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, function_name)
