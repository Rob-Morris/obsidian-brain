"""Bootstrap-safe, cross-process runtime warm-up coordination."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


STATUS_SCHEMA = "brain.runtime-status/1"
STATUS_REL = Path(".brain/local/runtime-status.json")
COORDINATION_LOCK_REL = Path(".brain/local/runtime-warmup.lock")
STATE_LOCK_REL = Path(".brain/local/runtime-status.lock")
RETRY_AFTER_MS = 250
WARMUP_STALE_SECONDS = 600


def read_runtime_status(vault_root: str | Path) -> dict[str, object]:
    """Return already-recorded status without probing, starting or writing."""

    root = Path(vault_root)
    path = root / STATUS_REL
    if not path.exists():
        return _cold_snapshot()
    if path.is_symlink() or not path.is_file():
        return _failed_snapshot("invalid_status_file", "Runtime status is not a regular file.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        state = _validate_state(raw)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _failed_snapshot("invalid_status_file", f"Runtime status is unreadable: {exc}")
    if state["core_version"] != _core_version(root):
        return _cold_snapshot()
    snapshot = _public_snapshot(state)
    if snapshot["state"] == "warming" and _warming_is_stale(snapshot):
        return _failed_snapshot(
            "warmup_timed_out",
            "Runtime warm-up stopped reporting progress before completion.",
            started_at=snapshot["started_at"],
        )
    return snapshot


def ensure_runtime_warmup(
    vault_root: str | Path,
    *,
    retry_failed: bool,
) -> tuple[str, dict[str, object]]:
    """Start or join one detached warm-up worker for the selected Brain."""

    from _common._file_lock import exclusive_file_lock

    root = Path(vault_root).resolve()
    local = root / ".brain/local"
    local.mkdir(parents=True, exist_ok=True)
    lock_path = root / COORDINATION_LOCK_REL
    if lock_path.is_symlink():
        return "failed", _failed_snapshot(
            "invalid_coordination_lock",
            "Runtime warm-up coordination lock cannot be a symlink.",
        )
    with exclusive_file_lock(lock_path, timeout=2.0):
        current = read_runtime_status(root)
        if current["state"] == "ready":
            return "already_ready", current
        if current["state"] == "warming":
            return "already_running", current
        if current["state"] == "failed" and not retry_failed:
            return "failed", current

        run_id = str(uuid.uuid4())
        started_at = _now()
        state = _state_document(
            root,
            run_id=run_id,
            state="warming",
            phase=None,
            router="not_started",
            lexical="not_started",
            semantic="not_started",
            started_at=started_at,
            last_error=None,
        )
        _write_state(root, state)
        try:
            _spawn_worker(root, run_id)
        except OSError as exc:
            failed = _state_document(
                root,
                run_id=run_id,
                state="failed",
                phase=None,
                router="not_started",
                lexical="not_started",
                semantic="not_started",
                started_at=started_at,
                last_error=_error(
                    "worker_start_failed",
                    "runtime",
                    f"Runtime warm-up worker could not start: {exc}",
                    True,
                ),
            )
            _write_state(root, failed)
            return "failed", _public_snapshot(failed)
        return "started", _public_snapshot(state)


def _spawn_worker(root: Path, run_id: str) -> None:
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(root), run_id],
        **kwargs,
    )


def _run_worker(root: Path, run_id: str) -> None:
    scripts = Path(__file__).resolve().parent.parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    from _common import load_compiled_router, vault_mutation_lock
    from _portable.lexical_maintenance import maintain_lexical_index
    from _portable.router_maintenance import maintain_router

    started_at = _current_started_at(root, run_id) or _now()
    current_component = "runtime"
    try:
        current_component = "router"
        _mark_phase(root, run_id, "router", started_at)
        with vault_mutation_lock(root):
            router_result = maintain_router(root, dry_run=False, force=False)
        if router_result.status == "partial":
            raise RuntimeError(router_result.session_error or "router warm-up was partial")
        _mark_component_ready(root, run_id, "router", started_at)

        current_component = "lexical"
        _mark_phase(root, run_id, "lexical", started_at)
        with vault_mutation_lock(root):
            maintain_lexical_index(root, dry_run=False, force=False)
        _mark_component_ready(root, run_id, "lexical", started_at)

        current_component = "semantic"
        _mark_phase(root, run_id, "semantic", started_at)
        semantic_state, semantic_error = _warm_semantic(root, load_compiled_router)
        _finish_ready(root, run_id, started_at, semantic_state, semantic_error)
    except Exception as exc:
        _finish_failed(root, run_id, started_at, current_component, exc)


def _warm_semantic(root: Path, load_compiled_router) -> tuple[str, dict | None]:
    import _semantic.config as semantic_config
    import _semantic.runtime as semantic_runtime
    from _lifecycle.retrieval_assets import (
        embeddings_should_refresh,
        refresh_embeddings_for_loaded_state,
    )
    from _search.lexical_query import load_index

    if not semantic_config.embeddings_enabled(root):
        return "disabled", None
    if not embeddings_should_refresh(root):
        return (
            "deferred",
            _error(
                "semantic_unavailable",
                "semantic",
                "Semantic warm-up is deferred because its managed runtime is unavailable.",
                False,
            ),
        )
    router = load_compiled_router(str(root))
    if "error" in router:
        raise RuntimeError(router["error"])
    index = load_index(str(root))
    type_embeddings, doc_embeddings, meta = semantic_runtime.load_embeddings_state(root)
    current = (
        type_embeddings is not None
        and doc_embeddings is not None
        and meta is not None
        and semantic_runtime.embeddings_meta_matches_router(meta, router)
    )
    if not current:
        refresh_embeddings_for_loaded_state(root, router, index["documents"])
    return "ready", None


def _mark_phase(root: Path, run_id: str, component: str, started_at: str) -> None:
    current = _read_owned_state(root, run_id)
    if current is None:
        raise RuntimeError("runtime warm-up was superseded")
    components = dict(current["components"])
    components[component] = "warming"
    _write_state(
        root,
        _state_document(
            root,
            run_id=run_id,
            state="warming",
            phase=component,
            router=components["router"],
            lexical=components["lexical"],
            semantic=components["semantic"],
            started_at=started_at,
            last_error=None,
        ),
    )


def _mark_component_ready(
    root: Path,
    run_id: str,
    component: str,
    started_at: str,
) -> None:
    current = _read_owned_state(root, run_id)
    if current is None:
        raise RuntimeError("runtime warm-up was superseded")
    components = dict(current["components"])
    components[component] = "ready"
    _write_state(
        root,
        _state_document(
            root,
            run_id=run_id,
            state="warming",
            phase=None,
            router=components["router"],
            lexical=components["lexical"],
            semantic=components["semantic"],
            started_at=started_at,
            last_error=None,
        ),
    )


def _finish_ready(
    root: Path,
    run_id: str,
    started_at: str,
    semantic: str,
    last_error: dict | None,
) -> None:
    if _read_owned_state(root, run_id) is None:
        return
    _write_state(
        root,
        _state_document(
            root,
            run_id=run_id,
            state="ready",
            phase=None,
            router="ready",
            lexical="ready",
            semantic=semantic,
            started_at=started_at,
            last_error=last_error,
        ),
    )


def _finish_failed(
    root: Path,
    run_id: str,
    started_at: str,
    component: str,
    exc: Exception,
) -> None:
    current = _read_owned_state(root, run_id)
    if current is None:
        return
    components = dict(current["components"])
    if component in components:
        components[component] = "failed"
    _write_state(
        root,
        _state_document(
            root,
            run_id=run_id,
            state="failed",
            phase=None,
            router=components["router"],
            lexical=components["lexical"],
            semantic=components["semantic"],
            started_at=started_at,
            last_error=_error(
                "warmup_failed",
                component if component in {"router", "lexical", "semantic"} else "runtime",
                str(exc) or type(exc).__name__,
                True,
            ),
        ),
    )


def _read_owned_state(root: Path, run_id: str) -> dict | None:
    path = root / STATUS_REL
    try:
        value = _validate_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if value["run_id"] == run_id else None


def _current_started_at(root: Path, run_id: str) -> str | None:
    state = _read_owned_state(root, run_id)
    return None if state is None else state["started_at"]


def _write_state(root: Path, value: dict[str, object]) -> None:
    from _common import safe_write
    from _common._file_lock import exclusive_file_lock

    local = root / ".brain/local"
    local.mkdir(parents=True, exist_ok=True)
    lock_path = root / STATE_LOCK_REL
    if lock_path.is_symlink():
        raise OSError("runtime status lock cannot be a symlink")
    with exclusive_file_lock(lock_path, timeout=2.0):
        safe_write(
            root / STATUS_REL,
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            bounds=local,
            follow_symlinks=False,
        )


def _state_document(
    root: Path,
    *,
    run_id: str,
    state: str,
    phase: str | None,
    router: str,
    lexical: str,
    semantic: str,
    started_at: str,
    last_error: dict | None,
) -> dict[str, object]:
    return {
        "schema": STATUS_SCHEMA,
        "core_version": _core_version(root),
        "run_id": run_id,
        "state": state,
        "phase": phase,
        "components": {
            "router": router,
            "lexical": lexical,
            "semantic": semantic,
        },
        "retry_after_ms": RETRY_AFTER_MS if state == "warming" else None,
        "started_at": started_at,
        "last_error": last_error,
    }


def _validate_state(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("runtime status must be an object")
    expected = {
        "schema",
        "core_version",
        "run_id",
        "state",
        "phase",
        "components",
        "retry_after_ms",
        "started_at",
        "last_error",
    }
    if set(value) != expected or value.get("schema") != STATUS_SCHEMA:
        raise ValueError("runtime status has an invalid schema")
    if value.get("state") not in {"cold", "warming", "ready", "failed"}:
        raise ValueError("runtime status state is invalid")
    if not isinstance(value.get("core_version"), str) or not isinstance(
        value.get("run_id"), str
    ):
        raise ValueError("runtime status identity is invalid")
    components = value.get("components")
    if not isinstance(components, dict) or set(components) != {
        "router",
        "lexical",
        "semantic",
    }:
        raise ValueError("runtime status components are invalid")
    if components["router"] not in {"not_started", "warming", "ready", "failed"}:
        raise ValueError("runtime router state is invalid")
    if components["lexical"] not in {"not_started", "warming", "ready", "failed"}:
        raise ValueError("runtime lexical state is invalid")
    if components["semantic"] not in {
        "disabled",
        "not_started",
        "warming",
        "ready",
        "deferred",
        "failed",
    }:
        raise ValueError("runtime semantic state is invalid")
    started_at = value.get("started_at")
    if not isinstance(started_at, str):
        raise ValueError("runtime status started_at is invalid")
    datetime.fromisoformat(started_at)
    return value


def _public_snapshot(state: dict) -> dict[str, object]:
    return {
        "schema": STATUS_SCHEMA,
        "state": state["state"],
        "phase": state["phase"],
        "components": dict(state["components"]),
        "retry_after_ms": state["retry_after_ms"],
        "started_at": state["started_at"],
        "last_error": state["last_error"],
    }


def _cold_snapshot() -> dict[str, object]:
    return {
        "schema": STATUS_SCHEMA,
        "state": "cold",
        "phase": None,
        "components": {
            "router": "not_started",
            "lexical": "not_started",
            "semantic": "not_started",
        },
        "retry_after_ms": None,
        "started_at": None,
        "last_error": None,
    }


def _failed_snapshot(
    code: str,
    message: str,
    *,
    started_at: object = None,
) -> dict[str, object]:
    return {
        "schema": STATUS_SCHEMA,
        "state": "failed",
        "phase": None,
        "components": {
            "router": "not_started",
            "lexical": "not_started",
            "semantic": "not_started",
        },
        "retry_after_ms": None,
        "started_at": started_at if isinstance(started_at, str) else None,
        "last_error": _error(code, "runtime", message, True),
    }


def _error(
    code: str,
    component: str,
    message: str,
    retryable: bool,
) -> dict[str, object]:
    return {
        "code": code,
        "component": component,
        "message": message,
        "retryable": retryable,
    }


def _warming_is_stale(snapshot: dict[str, object]) -> bool:
    started_at = snapshot.get("started_at")
    if not isinstance(started_at, str):
        return True
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return True
    if started.tzinfo is None:
        return True
    return (datetime.now(timezone.utc) - started).total_seconds() > WARMUP_STALE_SECONDS


def _core_version(root: Path) -> str:
    try:
        return (root / ".brain-core/VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    _run_worker(Path(sys.argv[2]).resolve(), sys.argv[3])
