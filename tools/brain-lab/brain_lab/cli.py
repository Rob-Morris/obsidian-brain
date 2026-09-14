from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .application import Application
from .baselines import register_baseline_handlers
from .docker import DockerClient
from .fixtures import register_fixture_handlers
from .operations import register_operational_handlers
from .process import DEFAULT_STREAM_LIMIT, CommandRunner
from .resources import register_resource_handlers
from .runs import register_run_handlers
from .scenarios import register_scenario_handlers
from .store import StateStore


VERSION = "0.3.0"


def build_application(
    *,
    state_directory: Path | None = None,
    docker_executable: str = "docker",
    stream_limit: int = DEFAULT_STREAM_LIMIT,
    credential_config: Path | None = None,
) -> Application:
    runner = CommandRunner(stream_limit=stream_limit)
    docker = DockerClient(runner, executable=docker_executable, credential_config=credential_config)
    tool_root = Path(__file__).resolve().parents[1]
    application = Application(
        store=StateStore(state_directory),
        runner=runner,
        docker=docker,
        tool_root=tool_root,
    )
    register_resource_handlers(application)
    register_baseline_handlers(application)
    register_run_handlers(application)
    register_fixture_handlers(application)
    register_scenario_handlers(application)
    register_operational_handlers(application)
    return application


def _parse_request(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if raw == "-":
        raw = sys.stdin.read()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"request JSON is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("request JSON must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain-lab",
        description="Build and operate disposable local Linux environments for Brain contributor work.",
    )
    parser.add_argument("--version", action="version", version=f"brain-lab {VERSION}")
    parser.add_argument("--state-dir", type=Path, help="Override the local lab receipt/evidence directory.")
    parser.add_argument("--docker", default="docker", help="Docker CLI executable (default: docker).")
    parser.add_argument("--registry-auth-config", type=Path, help="Explicit inline-auth Docker config.json for registry pulls/builds; output is redacted.")
    parser.add_argument(
        "--stream-limit",
        type=int,
        default=DEFAULT_STREAM_LIMIT,
        help="Maximum retained bytes for each stdout/stderr stream.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the canonical operation-result JSON envelope.")
    parser.add_argument(
        "resource",
        help="Resource noun, such as base, source, seed, baseline, run, fixture, or scenario.",
    )
    parser.add_argument("verb", help="Resource operation, such as build, capture, prepare, start, or exec.")
    parser.add_argument(
        "--request-json",
        metavar="JSON|-",
        help="Operation request object, or '-' to read it from standard input.",
    )
    return parser


def _human(result: dict[str, Any]) -> str:
    resource = result.get("resource") or {}
    identity = ""
    if resource:
        identity = f" {resource.get('kind')} {resource.get('id')}"
        if resource.get("docker_id"):
            identity += f" (Docker {resource['docker_id']})"
    lines = [f"{result['operation']}: {result['outcome']}{identity}"]
    lines.append(
        f"effects: {result['effect_certainty']}; evidence: {result['evidence_completeness']} ({result['evidence_bundle']})"
    )
    for error in result.get("errors", []):
        lines.append(f"error: {error['message']}")
    payload = result.get("payload", {})
    if payload.get("warning"):
        lines.append(f"warning: {payload['warning']}")
    return "\n".join(lines)


def _exit_code(result: dict[str, Any]) -> int:
    if result["effect_certainty"] == "unknown":
        return 4
    if result["effect_certainty"] == "partial":
        return 1
    if result["outcome"] == "success":
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = _parse_request(args.request_json)
        application = build_application(
            state_directory=args.state_dir,
            docker_executable=args.docker,
            stream_limit=args.stream_limit,
            credential_config=args.registry_auth_config,
        )
        operation = f"{args.resource}.{args.verb}"
        result = application.dispatch(operation, request).to_dict()
    except ValueError as exc:
        print(f"brain-lab: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(_human(result))
    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
