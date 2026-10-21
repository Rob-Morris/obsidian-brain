"""One text encoding contract for Python machine-process pipes."""
from __future__ import annotations

import os


def machine_process_options(options: dict) -> dict:
    """Pair UTF-8 decoding with child-only standard-stream encoding."""
    options = dict(options)
    environment = dict(os.environ if options.get("env") is None else options["env"])
    environment["PYTHONIOENCODING"] = "utf-8"
    options.update(env=environment, text=True, encoding="utf-8")
    return options
