#!/usr/bin/env python3
"""Cut a dev prefix onto unreleased, then publish the exact CI-tested version to main."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from _promotion import workflow
from _promotion import recovery_workflow
from _promotion.model import PromotionError, load_request

REPO_ROOT = Path(__file__).resolve().parents[2]

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status", help="list first-parent cuts on dev")
    status_parser.add_argument("--json", action="store_true")
    prepare_parser = sub.add_parser("prepare", help="build and push one candidate")
    prepare_parser.add_argument("--input", type=Path, required=True)
    finish_parser = sub.add_parser("finish", help="record one candidate on unreleased and dev")
    finish_parser.add_argument("branch")
    publish_parser = sub.add_parser("publish", help="fast-forward main to a finished version")
    publish_parser.add_argument("sha")
    adopt_parser = sub.add_parser("adopt", help="align with the winner and replay local-only dev commits")
    adopt_parser.add_argument("branch", nargs="?", help="explicit stale local candidate to discard after alignment")
    discard_parser = sub.add_parser("discard", help="delete one unpromoted candidate")
    discard_parser.add_argument("branch")
    sub.add_parser("pre-push", help="validate ref updates on stdin")
    recover = sub.add_parser("recover", help="rebuild an unpublished queue after a direct-main change")
    recovery_commands = recover.add_subparsers(dest="recovery_command", required=True)
    recovery_plan = recovery_commands.add_parser("plan", help="build a local immutable recovery plan")
    recovery_plan.add_argument("--input", type=Path, help="explicit Core/CLI/proxy version mappings")
    for action in ("stage", "status", "apply", "abort"):
        command = recovery_commands.add_parser(action)
        command.add_argument("plan_sha", help="full SHA of the sealed recovery plan")
        if action == "status":
            command.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    try:
        if args.command == "status":
            workflow.status(root, as_json=args.json)
        elif args.command == "prepare":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise PromotionError("promotion request must be a JSON object")
            workflow.prepare(root, load_request(payload))
        elif args.command == "finish":
            workflow.finish(root, args.branch)
        elif args.command == "publish":
            workflow.publish(root, args.sha)
        elif args.command == "adopt":
            workflow.adopt(root, args.branch)
        elif args.command == "discard":
            workflow.discard(root, args.branch)
        elif args.command == "pre-push":
            workflow.pre_push(root, sys.stdin.read().splitlines())
        elif args.command == "recover":
            if args.recovery_command == "plan":
                payload = None if args.input is None else json.loads(args.input.read_text(encoding="utf-8"))
                if payload is not None and not isinstance(payload, dict):
                    raise PromotionError("recovery request must be a JSON object")
                recovery_workflow.plan(root, version_map=payload)
            elif args.recovery_command == "stage":
                recovery_workflow.stage(root, args.plan_sha)
            elif args.recovery_command == "status":
                recovery_workflow.status(root, args.plan_sha, as_json=args.json)
            elif args.recovery_command == "apply":
                recovery_workflow.apply(root, args.plan_sha)
            elif args.recovery_command == "abort":
                recovery_workflow.abort(root, args.plan_sha)
        else:
            parser.error(f"unknown command {args.command}")
    except (OSError, PromotionError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"promotion: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
