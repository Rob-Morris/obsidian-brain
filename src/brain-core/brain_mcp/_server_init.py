from __future__ import annotations

import json

from . import _server_readiness
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
    payload["bootstrap_hint"] = _server_readiness.bootstrap_hint()
    return json.dumps(payload, ensure_ascii=False)
