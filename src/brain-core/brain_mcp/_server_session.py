from __future__ import annotations

import json
import os

import config as config_mod
import session

from . import _server_readiness
from ._server_runtime import ServerRuntime


def handle_brain_session(
    context: str | None,
    operator_key: str | None,
    runtime: ServerRuntime,
):
    runtime.check_version_drift()

    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    config_error = runtime.ensure_config_fresh("brain_session")
    if config_error is not None:
        return runtime.fmt_error(config_error)
    state = runtime.get_state()

    if state.config is not None:
        try:
            profile, _op_id = config_mod.authenticate_operator(operator_key, state.config)
            runtime.set_session_profile(profile)
        except ValueError as e:
            return runtime.fmt_error(str(e))

    state, progress = _server_readiness.require_router(runtime, "brain_session")
    if progress is not None:
        return progress
    runtime.refresh_cli_available()
    state = runtime.get_state()
    result = session.build_session_model(
        state.router,
        state.vault_root,
        obsidian_cli_available=state.cli_available,
        context=context,
        workspace_dir=(
            os.environ.get("BRAIN_WORKSPACE_DIR")
            or os.environ.get("BRAIN_PROJECT_DIR")
        ),
        config=state.config,
        active_profile=state.session_profile,
        load_config_if_missing=False,
    )

    try:
        session.persist_session_markdown(result, state.vault_root)
    except Exception:
        if state.logger:
            state.logger.error(
                "brain_session: failed to refresh session mirror",
                exc_info=True,
            )

    return json.dumps(result, ensure_ascii=False)
