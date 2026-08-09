#!/usr/bin/env python3
"""Regenerate the deterministic Phase 1 MCP metadata cost baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp_metadata_cost import CAPTURE_PATH, build_metadata_capture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=CAPTURE_PATH)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(build_metadata_capture(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
