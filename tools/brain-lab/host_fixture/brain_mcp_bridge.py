#!/usr/bin/env python3
"""Connect host stdio to one retained Brain Lab container's Brain MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    descriptor_path = Path(__file__).with_suffix(".json")
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if not isinstance(descriptor, dict):
            raise ValueError("descriptor root must be an object")
        if descriptor.get("schema") != "brain-lab.host-fixture-bridge/1":
            raise ValueError("unsupported schema")
        run_id = descriptor["run_id"]
        container_id = descriptor["container_id"]
        docker_executable = descriptor["docker_executable"]
        working_directory = descriptor["working_directory"]
        environment = descriptor["environment"]
        command = descriptor["command"]
        arguments = descriptor["args"]
        if not all(
            isinstance(value, str) and value and "\0" not in value
            for value in (
                run_id,
                container_id,
                docker_executable,
                working_directory,
                command,
            )
        ):
            raise ValueError("required fields must be non-empty strings")
        if not isinstance(environment, dict) or not all(
            isinstance(key, str)
            and "\0" not in key
            and isinstance(value, str)
            and "\0" not in value
            for key, value in environment.items()
        ):
            raise ValueError("environment must contain string fields")
        if not isinstance(arguments, list) or not all(
            isinstance(value, str) and "\0" not in value for value in arguments
        ):
            raise ValueError("args must be an array of strings")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"brain-lab fixture bridge is invalid: {exc}", file=sys.stderr)
        return 2

    argv = [
        docker_executable,
        "exec",
        "--interactive",
        "--workdir",
        working_directory,
    ]
    for key, value in sorted(environment.items()):
        argv.extend(["--env", f"{key}={value}"])
    argv.extend([container_id, command, *arguments])
    try:
        os.execvp(docker_executable, argv)
    except (OSError, ValueError) as exc:
        print(
            f"brain-lab fixture run {run_id} could not start its Docker bridge: {exc}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
