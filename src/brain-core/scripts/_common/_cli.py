"""CLI helpers shared across brain-core scripts."""

from __future__ import annotations

import argparse


class RaisingArgumentParser(argparse.ArgumentParser):
    """ArgumentParser variant that raises ValueError instead of exiting on errors."""

    def error(self, message):
        raise ValueError(message)


def require_option(parsed, attr_name, flag_name):
    """Require a parsed option while preserving unknown-flag rejection order.

    `argparse` with `required=True` reports missing required options before
    unknown flags. These benchmark CLIs intentionally keep the stricter
    "unrecognized arguments" behaviour for unknown options, so the required
    check runs after parsing succeeds.
    """
    if not getattr(parsed, attr_name):
        raise ValueError(f"the following arguments are required: {flag_name}")
