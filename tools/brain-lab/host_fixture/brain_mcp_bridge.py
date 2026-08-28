#!/usr/bin/env python3
"""Connect host stdio to one retained Brain Lab container's Brain MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    descriptor_path = Path(__file__).with_suffix(".json")
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        container_id = descriptor["container_id"]
        docker_executable = descriptor["docker_executable"]
        argv = descriptor["argv"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"brain-lab fixture bridge is invalid: {exc}", file=sys.stderr)
        return 2

    try:
        check = subprocess.run(
            [
                docker_executable,
                "container",
                "inspect",
                "--format",
                "{{json .State.Running}}",
                container_id,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"brain-lab fixture cannot inspect its selected run: {exc}", file=sys.stderr)
        return 3
    if check.returncode != 0:
        detail = check.stderr.strip() or "container is unavailable"
        print(
            f"brain-lab fixture run {descriptor['run_id']} is unavailable: {detail}",
            file=sys.stderr,
        )
        return 3
    if check.stdout.strip() != "true":
        print(f"brain-lab fixture run {descriptor['run_id']} is not running", file=sys.stderr)
        return 3
    os.execvp(argv[0], argv)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
