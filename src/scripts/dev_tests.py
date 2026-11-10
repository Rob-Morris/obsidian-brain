#!/usr/bin/env python3
"""Run, inspect, stop and follow up opt-in serial development tests."""
import argparse
import json
from pathlib import Path
import sys

from _test_control.records import read_json, summary
from _test_control.runner import REPOSITORY, combined, request, run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("run")
    start.add_argument("--root", type=Path, default=REPOSITORY)
    start.add_argument("--state-dir", type=Path)
    start.add_argument("--input", action="append", default=[])
    start.add_argument("pytest_args", nargs=argparse.REMAINDER)
    for name in ("status", "finish", "interrupt", "follow-up"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name in {"finish", "interrupt"}:
            command.add_argument("--reason", required=True)
        if name == "follow-up":
            command.add_argument("--state-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            pytest_args = args.pytest_args
            if pytest_args[:1] == ["--"]:
                pytest_args = pytest_args[1:]
            code, result = run(args.root, args.state_dir, pytest_args, args.input)
        elif args.command == "follow-up":
            launch = read_json(args.directory / "launch.json")
            code, result = run(Path(launch["root"]), args.state_dir, launch["pytest_args"],
                               launch["inputs"], previous=args.directory)
        elif args.command in {"finish", "interrupt"}:
            code, result = 0, request(args.directory, args.command, args.reason)
        else:
            code, result = 0, {**summary(args.directory), "combined": combined(args.directory)}
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "Controller interrupted; inspect the retained run receipt"}), file=sys.stderr)
        return 130
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
