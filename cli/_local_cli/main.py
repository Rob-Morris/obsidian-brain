"""CLI composition root for launcher and selected-Brain commands."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping
from types import SimpleNamespace
from _bootstrap.owner_attachment import OwnerAttachment, ProcessIdentity, PROCESS_CONTEXT_ENV
from _bootstrap.consent_owner import OwnerConnectionError, OwnerTransportUnavailable

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
    validate_application_envelope,
    LauncherCommandInvoker,
    LocalCliExecution,
    LocalExecutionProjection,
    SelectedBrainProcess,
    render_local_result,
)
from .parser import (
    discovery_request_argv,
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
    attachment = None
    try:
        attachment = OwnerAttachment.capture()
        return _run(argv, attachment)
    except (OwnerConnectionError, OwnerTransportUnavailable) as exc:
        print(f"brain: infrastructure — {exc}", file=sys.stderr)
        return 4
    finally:
        if attachment is not None:
            attachment.close()


def _run(argv, attachment) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        common, command_argv = _parse_common(argv)
        common.owner_attachment = attachment
        if common.version:
            print(f"brain {CLI_VERSION}")
            return 0
        if common.help or not command_argv:
            print(_help_text())
            return 0
        cli_binary, distribution_root = _trusted_distribution()
        if common.operation is not None and (not common.operation.strip() or len(common.operation.encode("utf-8")) > 128):
            raise LocalCliUsageError("--operation requires an ID of 1–128 UTF-8 bytes")
        if command_argv[:2] == ["session", "run"]:
            return _run_session(command_argv, common)
        if command_argv[:1] == ["command"]:
            if common.operation is not None:
                raise LocalCliUsageError("CLI discovery does not accept a prepared operation selector")
            selected = _resolve_optional(common, required=False)
            return _run_discovery(
                command_argv,
                common=common,
                selected=selected,
                cli_binary=cli_binary,
                distribution_root=distribution_root,
            )
        entry = _launcher_entry_for_argv(command_argv)
        if entry is not None and common.operation is not None:
            raise LocalCliUsageError("launcher commands do not accept application operation selectors")
        if entry is None:
            if len(command_argv) != 2:
                raise LocalCliUsageError(
                    "commands use one installed launcher entry point or exactly one "
                    "application noun and verb; pass request data with --request-json"
                )
            command_id = ".".join(command_argv)
            installed_launcher = _launcher_entry(command_id)
            if installed_launcher is not None:
                spelling = " ".join(installed_launcher.payload["entry_point"])
                raise LocalCliUsageError(
                    f"{command_id} uses the launcher entry point: {spelling}"
                )
        else:
            command_id = entry.command_id
        payload = _request_payload(common.request_json)
        selected = _resolve_for_command(common, entry, payload)
        if entry is not None:
            operator_key = _launcher_operator_key(command_id, common.operator_key)
            context = compose_launcher_context(
                cli_binary=cli_binary,
                distribution_root=distribution_root,
                selected=selected,
                dry_run=common.dry_run,
                operator_key=operator_key,
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
                    operator_key=common.operator_key,
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
    parser.add_argument("--operation")
    parser.add_argument("--request-json")
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
        attachment = getattr(common, "owner_attachment", None)
        inherited_job = attachment is not None and attachment.kind == "cli-job" and not explicit
        return resolve_selected_brain(
            vault=os.environ.get("BRAIN_VAULT_ROOT") if inherited_job else common.vault,
            brain_id=common.brain_id,
            workspace=os.environ.get("BRAIN_WORKSPACE_DIR") if inherited_job else common.workspace,
        )
    except Exception as exc:
        if required or explicit:
            raise CliError(str(exc)) from exc
        return None


def _resolve_for_command(common, entry, payload) -> SelectedBrain | None:
    if (
        entry is not None
        and entry.command_id in {"skill.expose", "skill.unexpose"}
        and payload.get("scope") == "project"
    ):
        from .runtime import resolve_project_exposure_brain

        try:
            return resolve_project_exposure_brain(
                vault=common.vault,
                brain_id=common.brain_id,
                workspace=common.workspace,
            )
        except Exception as exc:
            raise CliError(str(exc)) from exc
    return _resolve_optional(common, required=False)


def _request_payload(value: str | None) -> dict[str, object]:
    if value is None:
        return {}
    raw = sys.stdin.read() if value == "-" else value
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalCliUsageError(f"--request-json is invalid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise LocalCliUsageError("--request-json must decode to an object")
    return decoded


def _launcher_operator_key(command_id: str, supplied: str | None) -> str | None:
    if command_id != "permission.set-profile" or supplied:
        return supplied
    if not sys.stdin.isatty():
        raise LocalCliUsageError(
            "permission set-profile requires --operator-key when no interactive terminal is available"
        )
    value = getpass.getpass("Brain operator key: ")
    if not value.strip():
        raise LocalCliUsageError("permission set-profile requires a non-empty operator key")
    return value


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


def _launcher_entry_for_argv(command_argv: list[str]) -> ComposedCommandEntry | None:
    words = ("brain", *command_argv)
    match = next(
        (entry for entry in LAUNCHER_CATALOGUE.entries if entry.entry_point == words),
        None,
    )
    return _launcher_entry(match.command_id) if match is not None else None


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
        ),
        owner_attachment=getattr(common, "owner_attachment", None),
        operation_id=getattr(common, "operation", None),
    )


def _run_session(command_argv, common) -> int:
    """Start one explicitly selected job after checking the selected Brain contract."""
    if len(command_argv) < 4 or command_argv[2] != "--":
        raise LocalCliUsageError("use brain session run -- <program> [args...]")
    if common.request_json is not None or common.operation is not None or common.dry_run or common.json_mode:
        raise LocalCliUsageError("session run accepts target/credential options and a program after --")
    selected = _resolve_optional(common, required=True)
    # A new job does not reuse an outer job's principal or target ownership.
    discovery = SimpleNamespace(operator_key=common.operator_key, owner_attachment=None)
    envelope = _invoke_application(selected, "command.list", {"owner": "application", "page_size": 1},
                                   discovery, dependency_tier="stdlib")
    payload = _ok_payload(envelope, "command.list")
    if type(payload.get("interface_epoch")) is not int or payload["interface_epoch"] != 3:
        raise CliError("selected Brain must support command interface epoch 3; upgrade it before starting a consent job")
    from .session import run_owned_job
    return run_owned_job(selected, command_argv[3:], initialise_owner=lambda owner: _initialise_selected_job(selected, owner, common))


def _initialise_selected_job(selected, owner, common) -> None:
    """Run authentication in selected Brain code through a private supervisor-only handoff."""
    attachment = OwnerAttachment.for_job(owner)
    try:
        options = attachment.forwarded_process()
        options["env"][PROCESS_CONTEXT_ENV] = ProcessIdentity("cli-job", owner.identity.context_id).launch_value(initialise_owner=True)
        argv = [str(command_python(selected, "stdlib")), str(selected.command_script),
                "--initialise-job-owner", "--vault", str(selected.vault_root)]
        if common.operator_key is not None:
            argv.extend(("--operator-key", common.operator_key))
        if selected.workspace is not None:
            argv.extend(("--workspace", str(selected.workspace)))
        result = subprocess.run(argv, **options, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise CliError("selected Brain could not initialise the job: " + result.stderr.strip())
        expected = {"schema": "brain.owner-initialised/1", "context_id": owner.identity.context_id}
        if json.loads(result.stdout) != expected:
            raise CliError("selected Brain returned an invalid owner-initialisation acknowledgement")
    finally:
        attachment.close()


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
    attachment = getattr(common, "owner_attachment", None)
    options = attachment.forwarded_process() if attachment is not None else {}
    completed = subprocess.run(argv, capture_output=True, text=True, check=False, **options)
    if completed.returncode not in range(5):
        raise CliError("selected Brain discovery failed its structural process contract")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CliError("selected Brain discovery returned invalid JSON") from exc
    try:
        validate_application_envelope(result, command_id, completed.returncode)
    except RuntimeError as exc:
        raise CliError(str(exc)) from exc
    return result


def _run_discovery(command_argv, *, common, selected, cli_binary, distribution_root) -> int:
    if common.request_json is not None:
        command_argv = discovery_request_argv(command_argv, _request_payload(common.request_json))
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
        catalogues = {}
        if application is not None:
            catalogues["application"] = {
                "schema": application.catalogue_schema,
                "fingerprint": application.catalogue_fingerprint,
            }
        if launcher is not None:
            catalogues["launcher"] = {
                "schema": launcher.schema, "fingerprint": launcher.catalogue_fingerprint,
            }
        brief_fields = ("command_id", "command_version", "summary", "authority",
                        "effect_class", "availability", "access", "entry_point")
        entries = []
        for item in composed.entries:
            fields = (dict(item.payload) if arguments.view == "detailed" or item.owner == "application" else
                      {key: item.payload[key] for key in brief_fields if key in item.payload})
            entries.append({**fields, "owner": item.owner})
        payload = {
            "schema": "brain.local-command-list/2",
            "catalogues": catalogues,
            "entries": entries,
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
    return """brain 3.0 — canonical Brain command interface

Usage:
  brain <launcher-entry-point> [--request-json JSON|-] [--vault PATH|--brain ID] [--json]
  brain <noun> <verb> [--request-json JSON|-] [--vault PATH|--brain ID] [--json]
  brain command list [--owner application|launcher|all] [filters] [--json]
  brain command describe <command-id> [--owner application|launcher|all] [--json]
  brain session run [--vault PATH|--brain ID] [--operator-key KEY] -- <program> [args...]
  brain --version

Application commands use their canonical noun/verb spelling. Launcher commands
use the entry point advertised by discovery. Run `brain command describe
<command-id> --json` for its exact request schema and minimal example.
Use --operation ID on an application invocation to select explicitly authorised
specific consent. A session job ends when its root program exits.
"""


if __name__ == "__main__":
    main()
