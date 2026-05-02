from __future__ import annotations

import json

from ._server_runtime import ServerRuntime


def handle_brain_init(
    warmup: bool,
    debug: bool,
    runtime: ServerRuntime,
):
    """Return the additive Brain bootstrap snapshot."""
    runtime.check_version_drift()
    if warmup:
        runtime.ensure_warmup_started("brain_init")

    payload = runtime.get_readiness_snapshot(debug=debug)
    payload["bootstrap_hint"] = (
        "Call `brain_session` when you start real Brain work."
    )
    return json.dumps(payload, ensure_ascii=False)
