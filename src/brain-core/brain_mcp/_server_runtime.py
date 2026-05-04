from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ServerState:
    vault_root: str | None
    loaded_version: str | None
    config: dict | None
    session_profile: str | None
    router: dict | None
    index: dict | None
    cli_available: bool
    vault_name: str | None
    workspace_registry: dict | None
    logger: Any


@dataclass(frozen=True)
class ServerRuntime:
    get_state: Callable[[], ServerState]
    set_router: Callable[[dict | None], None]
    set_index: Callable[[dict | None], None]
    set_workspace_registry: Callable[[dict | None], None]
    set_session_profile: Callable[[str | None], None]
    fmt_error: Callable[[str], Any]
    fmt_progress: Callable[[str, tuple[str, ...]], Any]
    enforce_profile: Callable[[str], Any]
    refresh_cli_available: Callable[[], bool]
    ensure_warmup_started: Callable[[str | None], None]
    ensure_router_fresh: Callable[[], None]
    ensure_index_fresh: Callable[[], None]
    ensure_mutation_index_ready: Callable[[], None]
    get_readiness_snapshot: Callable[[bool], dict[str, Any]]
    check_version_drift: Callable[[], None]
    mark_index_dirty: Callable[[], None]
    mark_index_pending: Callable[[str, str | None], None]
    mark_router_dirty: Callable[[], None]
    compile_and_save: Callable[[str], dict]
    build_index_and_save: Callable[[str], dict]
    refresh_session_mirror_best_effort: Callable[[], None]
