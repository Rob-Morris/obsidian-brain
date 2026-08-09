#!/usr/bin/env python3
"""Direct noun/verb entry point for selected-Brain application commands."""

from __future__ import annotations

from pathlib import Path

from _command_interface.script import run


if __name__ == "__main__":
    raise SystemExit(run(script_path=Path(__file__)))
