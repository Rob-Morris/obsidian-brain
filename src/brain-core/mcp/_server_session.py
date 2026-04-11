from __future__ import annotations

import json

import config as config_mod
import session

from _server_runtime import ServerRuntime


def handle_brain_session(
    context: str | None,
    operator_key: str | None,
    runtime: ServerRuntime,
):
    runtime.check_version_drift()
    runtime.ensure_router_fresh()
    runtime.refresh_cli_available()

    state = runtime.get_state()
    if state.router is None or state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    if state.config is not None:
        try:
            profile, _op_id = config_mod.authenticate_operator(operator_key, state.config)
            runtime.set_session_profile(profile)
        except ValueError as e:
            return runtime.fmt_error(str(e))
    else:
        runtime.set_session_profile(None)

    state = runtime.get_state()
    result = session.compile_session(
        state.router,
        state.vault_root,
        obsidian_cli_available=state.cli_available,
        context=context,
        config=state.config,
    )

    if state.session_profile:
        result["active_profile"] = state.session_profile

    return json.dumps(result, ensure_ascii=False)
