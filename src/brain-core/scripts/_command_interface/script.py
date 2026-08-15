"""Stable direct-script grammar and presentation over the shared adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback
from typing import TextIO

from _application.adapter import AdapterRequestError, ApplicationAdapter
from _application.projection import command_id_from_argv
from _application.registry import current_application_catalogue, current_request_resolver

from .direct import (
    DirectContextError,
    compose_direct_context,
    direct_script_command_ids,
    resolve_direct_vault,
)


class ScriptUsageError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ScriptUsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="command.py",
        description="Invoke one selected-Brain command through canonical noun/verb grammar.",
    )
    parser.add_argument("noun", help="Canonical command noun.")
    parser.add_argument("verb", help="Canonical command verb.")
    parser.add_argument(
        "--request-json",
        default="{}",
        help="Command request object as JSON, or '-' to read one object from stdin.",
    )
    parser.add_argument("--vault", help="Selected Brain vault root.")
    parser.add_argument("--workspace", help="Bound caller workspace directory.")
    parser.add_argument("--operator-key", help="Operator key for profile authentication.")
    parser.add_argument("--dry-run", action="store_true", help="Request no committed effects.")
    parser.add_argument("--json", action="store_true", help="Emit only canonical JSON on stdout.")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    context_factory=compose_direct_context,
    script_path: Path | None = None,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        catalogue = current_application_catalogue()
        resolver = current_request_resolver()
    except Exception as exc:
        _report_internal_failure(
            "command catalogue failed to load",
            exc,
            stderr,
        )
        return 4
    try:
        args = build_parser().parse_args(argv)
        command_id = command_id_from_argv(
            args.noun,
            args.verb,
            direct_script_command_ids(catalogue),
        )
        payload = _request_payload(args.request_json, stdin)
    except (ScriptUsageError, ValueError, json.JSONDecodeError) as exc:
        print(f"command.py: invalid request — {exc}", file=stderr)
        return 2
    try:
        vault_root = resolve_direct_vault(args.vault, script_path=script_path)
        workspace = Path(args.workspace).expanduser() if args.workspace else None
        if workspace is not None and not workspace.is_absolute():
            workspace = (Path.cwd() / workspace).resolve()
        context = context_factory(
            vault_root=vault_root,
            command_id=command_id,
            catalogue=catalogue,
            operator_key=args.operator_key,
            workspace_dir=workspace,
            dry_run=args.dry_run,
        )
    except (DirectContextError, OSError, ValueError) as exc:
        print(f"command.py: infrastructure — {exc}", file=stderr)
        return 4
    try:
        adapter = ApplicationAdapter(
            catalogue,
            resolver,
        )
        projection = adapter.invoke(context, command_id, payload)
    except AdapterRequestError as exc:
        print(f"{command_id}: {exc.code.value} — {exc}", file=stderr)
        return 2
    except Exception as exc:
        _report_internal_failure("direct command setup failed", exc, stderr)
        return 4
    if args.json:
        print(projection.json_text, file=stdout)
    elif projection.is_error:
        print(projection.concise_text, file=stderr)
    else:
        print(projection.concise_text, file=stdout)
    return projection.exit_code


def _request_payload(value: str, stdin: TextIO) -> dict[str, object]:
    raw = stdin.read() if value == "-" else value
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ScriptUsageError("--request-json must decode to an object")
    return decoded


def _report_internal_failure(message: str, error: Exception, stderr: TextIO) -> None:
    print(f"command.py: internal_error — {message}", file=stderr)
    traceback.print_exception(
        type(error),
        error,
        error.__traceback__,
        file=stderr,
    )
