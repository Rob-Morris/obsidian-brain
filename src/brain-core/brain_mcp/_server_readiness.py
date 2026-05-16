from __future__ import annotations

from typing import Any, Literal

from ._server_runtime import ReadinessInfo, ServerRuntime, ServerState

CapabilityNeed = Literal["router", "index", "semantic"]
ReadinessState = Literal["cold", "warming", "ready", "failed"]
WarmupState = Literal["not_started", "running", "complete", "failed"]
SemanticWarmupState = Literal["disabled", "warming", "ready", "deferred"]


def bootstrap_hint() -> str:
    """Return the canonical lightweight bootstrap hint."""
    return next_action("ready", "complete")


def next_action(
    readiness: ReadinessState | str,
    warmup_state: WarmupState | str,
    *,
    tool_name: str | None = None,
    needs: tuple[CapabilityNeed, ...] = (),
) -> str:
    """Return the immediate-action hint for the current coarse readiness state."""
    next_tool = tool_name or "brain_session"
    if needs and warmup_state not in {"failed", "not_started"}:
        return f"Retry `{next_tool}` shortly while Brain warmup continues."
    if readiness == "ready":
        return "Call `brain_session` when you start real Brain work."
    if warmup_state == "failed":
        return (
            f"Retry `{next_tool}` or call `brain_init(warmup=true, debug=true)` "
            "to restart warmup and inspect cheap diagnostics."
        )
    if warmup_state == "not_started":
        return (
            f"Call `{next_tool}` or `brain_init(warmup=true)` to start background warmup."
        )
    return f"Retry `{next_tool}` shortly while Brain warmup continues."


def build_snapshot(
    *,
    state: ServerState,
    info: ReadinessInfo,
    debug: bool = False,
    tool_name: str | None = None,
    needs: tuple[CapabilityNeed, ...] = (),
) -> dict[str, Any]:
    """Build the coarse readiness snapshot returned by bootstrap/progress calls."""
    payload: dict[str, Any] = {
        "version": "1",
        "brain_core_version": state.loaded_version,
        "vault_root": state.vault_root,
        "vault_name": state.vault_name,
        "readiness": info.readiness,
        "warmup_state": info.warmup_state,
        "next_action": next_action(
            info.readiness,
            info.warmup_state,
            tool_name=tool_name,
            needs=needs,
        ),
    }
    if info.active_phase:
        payload["active_phase"] = info.active_phase
    if info.last_error:
        payload["last_error"] = info.last_error
    if debug:
        payload["debug"] = {
            "active_phase": info.active_phase,
            "last_error": info.last_error,
            "last_reason": info.last_reason,
            "router_ready": state.router is not None,
            "index_ready": state.index is not None,
            "workspace_registry_ready": state.workspace_registry is not None,
            "semantic_warmup_state": info.semantic_warmup_state,
        }
    return payload


def require_router(
    runtime: ServerRuntime,
    tool_name: str,
) -> tuple[ServerState | None, Any | None]:
    """Gate a tool on router readiness and return the refreshed server state."""
    runtime.ensure_warmup_started(tool_name)
    state = runtime.get_state()
    if state.router is None:
        return None, runtime.fmt_progress(tool_name, ("router",))

    runtime.ensure_router_fresh()
    state = runtime.get_state()
    if state.router is None:
        return None, runtime.fmt_progress(tool_name, ("router",))
    return state, None


def require_index(
    runtime: ServerRuntime,
    tool_name: str,
) -> tuple[ServerState | None, Any | None]:
    """Gate a tool on full search-index readiness."""
    state, progress = require_router(runtime, tool_name)
    if progress is not None:
        return None, progress

    runtime.ensure_index_fresh()
    state = runtime.get_state()
    if state.index_error:
        return None, runtime.fmt_error(state.index_error)
    if state.index is None:
        return None, runtime.fmt_progress(tool_name, ("index",))
    return state, None


def require_mutation_index(
    runtime: ServerRuntime,
    tool_name: str,
) -> tuple[ServerState | None, Any | None]:
    """Gate a mutation path on bounded-cost index readiness."""
    runtime.ensure_mutation_index_ready()
    state = runtime.get_state()
    if state.index is None:
        return None, runtime.fmt_progress(tool_name, ("index",))
    return state, None


def require_semantic(runtime: ServerRuntime, tool_name: str) -> Any | None:
    """Gate a tool on semantic readiness using the shared semantic policy."""
    info = runtime.get_readiness_info()
    if info.semantic_warmup_state == "warming":
        return runtime.fmt_progress(tool_name, ("semantic",))
    if info.semantic_warmup_state == "deferred":
        detail = info.last_semantic_error or (
            "semantic warmup failed unexpectedly; restart the server to retry semantic warmup"
        )
        return runtime.fmt_error(detail)
    runtime.ensure_embeddings_fresh()
    return None
