"""CLI 2 composition root for launcher and selected-Brain commands."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.adapter import LauncherAdapter
from _launcher.discovery import describe_command, list_commands
from _launcher.owners import LAUNCHER_OWNERS

from .discovery import (
    ApplicationDiscoveryPage,
    ComposedCommandEntry,
    compose_application_description,
    compose_launcher_description,
    compose_list,
)
from .execution import (
    ApplicationProcessInvoker,
    LauncherCommandInvoker,
    LocalCliExecution,
    LocalExecutionProjection,
    SelectedBrainProcess,
    render_local_result,
)
from .parser import (
    CommandDescribeArguments,
    CommandListArguments,
    LocalCliUsageError,
    parse_discovery_arguments,
    parser_exit_code,
)
from .runtime import (
    CLI_VERSION,
    SelectedBrain,
    command_python,
    compose_launcher_context,
    resolve_selected_brain,
)


class CliError(RuntimeError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LocalCliUsageError(message)


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        common, command_argv = _parse_common(argv)
        if common.version:
            print(f"brain {CLI_VERSION}")
            return 0
        if common.help or not command_argv:
            print(_help_text())
            return 0
        cli_binary, distribution_root = _trusted_distribution()
        selected = _resolve_optional(common, required=False)
        if command_argv[:1] == ["command"]:
            return _run_discovery(
                command_argv,
                common=common,
                selected=selected,
                cli_binary=cli_binary,
                distribution_root=distribution_root,
            )
        if len(command_argv) != 2:
            raise LocalCliUsageError(
                "commands use exactly one canonical noun and verb; "
                "pass request data with --request-json"
            )
        command_id = ".".join(command_argv)
        payload = _request_payload(common.request_json)
        entry = _launcher_entry(command_id)
        if entry is not None:
            context = compose_launcher_context(
                cli_binary=cli_binary,
                distribution_root=distribution_root,
                selected=selected,
                dry_run=common.dry_run,
            )
            projection = LocalCliExecution(
                (
                    _unreachable_application_invoker(),
                    LauncherCommandInvoker(
                        context,
                        LauncherAdapter(LAUNCHER_CATALOGUE, LAUNCHER_OWNERS),
                    ),
                )
            ).invoke(entry, payload)
        else:
            selected = selected or _resolve_optional(common, required=True)
            if not selected.supports_command_interface:
                projection = _upgrade_required(command_id, selected)
            else:
                entry = _application_entry(selected, command_id, common)
                invoker = _application_invoker(selected, entry, common)
                context = compose_launcher_context(
                    cli_binary=cli_binary,
                    distribution_root=distribution_root,
                    selected=selected,
                    dry_run=common.dry_run,
                )
                projection = LocalCliExecution(
                    (
                        invoker,
                        LauncherCommandInvoker(
                            context,
                            LauncherAdapter(LAUNCHER_CATALOGUE, LAUNCHER_OWNERS),
                        ),
                    )
                ).invoke(entry, payload)
        stdout, stderr, code = render_local_result(projection, json_mode=common.json_mode)
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        return code
    except LocalCliUsageError as exc:
        print(f"brain: invalid request — {exc}", file=sys.stderr)
        return parser_exit_code()
    except (CliError, OSError, ValueError) as exc:
        print(f"brain: infrastructure — {exc}", file=sys.stderr)
        return 4


def main() -> None:
    raise SystemExit(run())


def _parse_common(argv: list[str]):
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--vault")
    parser.add_argument("--brain", dest="brain_id")
    parser.add_argument("--workspace")
    parser.add_argument("--operator-key")
    parser.add_argument("--request-json", default="{}")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", dest="json_mode", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--help", "-h", dest="help", action="store_true")
    return parser.parse_known_args(argv)


def _trusted_distribution() -> tuple[Path, Path]:
    binary = os.environ.get("BRAIN_CLI_BINARY")
    root = os.environ.get("BRAIN_CLI_DISTRIBUTION_ROOT")
    if not binary or not root:
        raise CliError("the CLI bootloader did not supply its distribution identity")
    binary_candidate = Path(binary).expanduser()
    root_candidate = Path(root).expanduser()
    if binary_candidate.is_symlink() or not binary_candidate.is_file():
        raise CliError("the CLI binary path is missing or unsafe")
    if root_candidate.is_symlink():
        raise CliError("the CLI distribution is missing or unsafe")
    binary_path = binary_candidate.resolve()
    root_path = root_candidate.resolve()
    if not (root_path / "cli" / "launcher_catalogue.py").is_file():
        raise CliError("the CLI distribution is missing or unsafe")
    return binary_path, root_path


def _resolve_optional(common, *, required: bool) -> SelectedBrain | None:
    explicit = any((common.vault, common.brain_id, common.workspace))
    try:
        return resolve_selected_brain(
            vault=common.vault,
            brain_id=common.brain_id,
            workspace=common.workspace,
        )
    except Exception as exc:
        if required or explicit:
            raise CliError(str(exc)) from exc
        return None


def _request_payload(value: str) -> dict[str, object]:
    raw = sys.stdin.read() if value == "-" else value
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalCliUsageError(f"--request-json is invalid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise LocalCliUsageError("--request-json must decode to an object")
    return decoded


def _launcher_entry(command_id: str) -> ComposedCommandEntry | None:
    page = list_commands(query=command_id, page_size=500)
    summary = next((item for item in page.entries if item.command_id == command_id), None)
    if summary is None:
        return None
    composed = compose_list(owner="launcher", launcher=type(page)(
        page.schema,
        page.catalogue_fingerprint,
        (summary,),
        None,
    ))
    return composed.entries[0]


def _application_entry(selected: SelectedBrain, command_id: str, common) -> ComposedCommandEntry:
    envelope = _invoke_application(
        selected,
        "command.describe",
        {"target_command_id": command_id},
        common,
        dependency_tier="bootstrap",
    )
    payload = _ok_payload(envelope, "command.describe")
    description = compose_application_description(
        catalogue_schema=_required_text(payload, "catalogue_schema"),
        catalogue_fingerprint=_required_text(payload, "catalogue_fingerprint"),
        payload=payload,
    )
    summary = _required_text(payload, "summary")
    return ComposedCommandEntry(
        description.owner,
        description.catalogue_schema,
        description.catalogue_fingerprint,
        description.command_id,
        description.command_version,
        summary,
        payload,
    )


def _application_invoker(selected: SelectedBrain, entry: ComposedCommandEntry, common):
    tier = entry.payload.get("dependency_tier")
    if not isinstance(tier, str):
        raise CliError("selected Brain description omitted dependency_tier")
    return ApplicationProcessInvoker(
        SelectedBrainProcess(
            selected.vault_root,
            command_python(selected, tier),
            selected.workspace,
            common.operator_key,
            common.dry_run,
        )
    )


def _unreachable_application_invoker():
    missing = Path("/nonexistent/brain").resolve()
    return ApplicationProcessInvoker(SelectedBrainProcess(missing, Path(sys.executable).resolve()))


def _invoke_application(
    selected: SelectedBrain,
    command_id: str,
    payload: Mapping[str, object],
    common,
    *,
    dependency_tier: str,
) -> Mapping[str, object]:
    noun, verb = command_id.split(".", 1)
    argv = [
        str(command_python(selected, dependency_tier)),
        str(selected.command_script),
        noun,
        verb,
        "--request-json",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "--vault",
        str(selected.vault_root),
        "--json",
    ]
    if selected.workspace is not None:
        argv.extend(("--workspace", str(selected.workspace)))
    if common.operator_key:
        argv.extend(("--operator-key", common.operator_key))
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.stderr or completed.returncode not in range(5):
        raise CliError("selected Brain discovery failed its structural process contract")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CliError("selected Brain discovery returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise CliError("selected Brain discovery returned a non-object result")
    return result


def _run_discovery(command_argv, *, common, selected, cli_binary, distribution_root) -> int:
    arguments = parse_discovery_arguments(command_argv)
    if isinstance(arguments, CommandListArguments):
        application = None
        launcher = None
        if arguments.owner in {"application", "all"}:
            selected = selected or _resolve_optional(common, required=True)
            if not selected.supports_command_interface:
                raise CliError(
                    f"selected Brain {selected.version or 'unknown'} requires upgrade before application discovery"
                )
            envelope = _invoke_application(
                selected,
                "command.list",
                arguments.application_payload() or {},
                common,
                dependency_tier="bootstrap",
            )
            payload = _ok_payload(envelope, "command.list")
            entries = payload.get("entries")
            if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
                raise CliError("selected Brain command.list returned invalid entries")
            application = ApplicationDiscoveryPage(
                _required_text(payload, "catalogue_schema"),
                _required_text(payload, "catalogue_fingerprint"),
                tuple(entries),
                payload.get("next_cursor") if isinstance(payload.get("next_cursor"), dict) else None,
            )
        if arguments.owner in {"launcher", "all"}:
            launcher = list_commands(
                providers=compose_launcher_context(
                    cli_binary=cli_binary,
                    distribution_root=distribution_root,
                    selected=selected,
                    dry_run=False,
                ).providers,
                **(arguments.launcher_filters() or {}),
            )
        composed = compose_list(owner=arguments.owner, application=application, launcher=launcher)
        payload = {
            "schema": "brain.local-command-list/1",
            "entries": [asdict(item) for item in composed.entries],
            "application_next_cursor": composed.application_next_cursor,
            "launcher_next_cursor": composed.launcher_next_cursor,
        }
        return _render_discovery(payload, common.json_mode)

    assert isinstance(arguments, CommandDescribeArguments)
    launcher_description = None
    if arguments.owner in {"launcher", "all"}:
        try:
            launcher_description = describe_command(arguments.target_command_id)
        except KeyError:
            launcher_description = None
    if launcher_description is not None:
        description = compose_launcher_description(
            launcher_description,
            catalogue_schema=LAUNCHER_CATALOGUE.schema,
            catalogue_fingerprint=LAUNCHER_CATALOGUE.fingerprint,
        )
    else:
        if arguments.owner == "launcher":
            raise LocalCliUsageError("launcher command is not installed")
        selected = selected or _resolve_optional(common, required=True)
        if not selected.supports_command_interface:
            raise CliError("selected Brain requires upgrade before application discovery")
        envelope = _invoke_application(
            selected,
            "command.describe",
            {"target_command_id": arguments.target_command_id},
            common,
            dependency_tier="bootstrap",
        )
        payload = _ok_payload(envelope, "command.describe")
        description = compose_application_description(
            catalogue_schema=_required_text(payload, "catalogue_schema"),
            catalogue_fingerprint=_required_text(payload, "catalogue_fingerprint"),
            payload=payload,
        )
    return _render_discovery(
        {"schema": "brain.local-command-description/1", **asdict(description)},
        common.json_mode,
    )


def _ok_payload(envelope: Mapping[str, object], command_id: str) -> Mapping[str, object]:
    if envelope.get("schema") != "brain.command-result/1" or envelope.get("command") != command_id:
        raise CliError(f"selected Brain {command_id} returned the wrong contract")
    if envelope.get("status") != "ok" or not isinstance(envelope.get("result"), dict):
        error = envelope.get("error")
        message = error.get("message") if isinstance(error, dict) else "discovery failed"
        raise CliError(str(message))
    return envelope["result"]


def _upgrade_required(command_id: str, selected: SelectedBrain) -> LocalExecutionProjection:
    envelope = {
        "schema": "brain.command-result/1",
        "command": command_id,
        "command_version": 1,
        "status": "error",
        "warnings": [],
        "result": None,
        "error": {
            "code": "upgrade_required",
            "message": "The selected Brain predates the canonical command interface.",
            "details": {
                "brain_core_version": selected.version,
                "required_version": ".".join(str(item) for item in (0, 55, 0)),
            },
            "next_action": {"command": "brain.upgrade", "arguments": {}},
            "effects": "none",
            "retryable": False,
        },
    }
    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return LocalExecutionProjection(
        "application",
        command_id,
        1,
        envelope,
        text,
        f"{command_id}: upgrade_required — {envelope['error']['message']}",
        True,
        4,
    )


def _render_discovery(payload: Mapping[str, object], json_mode: bool) -> int:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0
    entries = payload.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            print(f"{entry['command_id']}\t{entry['owner']}\t{entry['summary']}")
    else:
        print(f"{payload['command_id']}\t{payload['owner']}")
        inner = payload.get("payload")
        if isinstance(inner, dict) and isinstance(inner.get("summary"), str):
            print(inner["summary"])
    return 0


def _required_text(payload: Mapping[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise CliError(f"selected Brain discovery omitted {name}")
    return value


def _help_text() -> str:
    return """brain 2.0 — canonical Brain command interface

Usage:
  brain <noun> <verb> [--request-json JSON|-] [--vault PATH|--brain ID] [--json]
  brain command list [--owner application|launcher|all] [filters] [--json]
  brain command describe <command-id> [--owner application|launcher|all] [--json]
  brain --version

Every semantic command uses one canonical noun/verb spelling. Run `brain command
describe <command-id> --json` for its exact request schema and minimal example.
"""


if __name__ == "__main__":
    main()
