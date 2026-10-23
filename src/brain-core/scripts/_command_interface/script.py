"""Stable direct-script grammar and presentation over the shared adapter."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import errno
import hashlib
import json
import os
import secrets
import socket
import stat
from pathlib import Path
import sys
from typing import TextIO
from _bootstrap.file_lock import MutationLockError, exclusive_file_lock
from _bootstrap.owner_attachment import OwnerAttachment, capture_process_identity
from _bootstrap.consent_owner import OwnerConnectionError, OwnerTransportUnavailable
from _bootstrap import private_files

from _application.adapter import AdapterRequestError, ApplicationAdapter, project_delivery_failure
from _application.context import report_failure_safely
from _application.delivery_failure import DeliveryFailure
from _application.projection import command_id_from_argv
from _application.registry import current_application_catalogue, current_request_resolver

from .direct import (
    RESULT_STORAGE_OPTIONAL_COMMANDS,
    DirectContextError,
    InvalidResultSession,
    WorkspaceCompatibilityError,
    compose_direct_context,
    direct_script_command_ids,
    resolve_direct_vault,
)

RESULT_SESSION_UNAVAILABLE_MESSAGE = (
    "Private result-session credential delivery could not be verified. "
    "Retained continuation is unavailable; the source outcome is unchanged. "
    "Do not rerun a mutation to recover output."
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
    parser.add_argument("--result-session-file", help="Private credential file; session start creates it if absent.")
    parser.add_argument("--operation", help="Prepared operation ID selecting specific consent in this context.")
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
    owner_attachment=None,
    transport_identity=None,
    owner_initialisation_allowed=False,
) -> int:
    attachment = owner_attachment
    result_locks = ExitStack()
    try:
        if attachment is None:
            attachment = OwnerAttachment.capture()
        if transport_identity is None:
            transport_identity, owner_initialisation_allowed = capture_process_identity()
        return _run(argv, stdin=stdin, stdout=stdout, stderr=stderr,
                    context_factory=context_factory, script_path=script_path,
                    owner_attachment=attachment, transport_identity=transport_identity,
                    owner_initialisation_allowed=owner_initialisation_allowed, result_locks=result_locks)
    except (OSError, MutationLockError):
        print("command.py: infrastructure — private result-client file unavailable", file=stderr or sys.stderr)
        return 4
    except (OwnerConnectionError, OwnerTransportUnavailable) as exc:
        print(f"command.py: infrastructure — {exc}", file=stderr or sys.stderr)
        return 4
    finally:
        result_locks.close()
        if attachment is not None:
            attachment.close()


def initialise_job(argv, *, owner_attachment, transport_identity, owner_initialisation_allowed) -> int:
    """Private selected-Brain initialisation; public CLI commands cannot grant this permission."""
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--initialise-job-owner", action="store_true", required=True)
    parser.add_argument("--result-session-file")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--operator-key")
    parser.add_argument("--workspace")
    seed_attachment = None
    try:
        args = parser.parse_args(argv)
        if (owner_attachment is None or owner_attachment.kind != "cli-job"
                or transport_identity is None or transport_identity.kind != "cli-job"
                or not owner_initialisation_allowed):
            raise PermissionError("job initialisation requires the supervisor's private launch permission")
        session_path = Path(args.result_session_file).expanduser().absolute() if args.result_session_file else None
        result_session = _read_result_session(session_path, "session.start") if session_path is not None else None
        if session_path is not None:
            # Identity setup closes its connection. Preserve a distinct private
            # rendezvous for authenticated composition after that setup.
            descriptor, = owner_attachment.forwarded_process()["pass_fds"]
            seed_attachment = OwnerAttachment(socket.socket(fileno=os.dup(descriptor)), "job")
        from .direct import initialise_attached_job_owner
        initialise_attached_job_owner(
            vault_root=Path(args.vault), owner_attachment=owner_attachment,
            transport_identity=transport_identity, operator_key=args.operator_key,
            workspace_dir=Path(args.workspace) if args.workspace else None,
        )
        if seed_attachment is not None:
            context = compose_direct_context(
                vault_root=Path(args.vault), command_id="session.start",
                workspace_dir=Path(args.workspace) if args.workspace else None,
                owner_attachment=seed_attachment, transport_identity=transport_identity,
                owner_initialisation_allowed=False, result_session=result_session,
            )
            if result_session == "new":
                credential = context.result_delivery.credential
                if credential is None:
                    raise ValueError("Result-session issuance unavailable.")
                _write_result_session(session_path, credential)
        print(json.dumps({"schema": "brain.owner-initialised/1", "context_id": transport_identity.context_id}))
        return 0
    except DeliveryFailure as exc:
        print(f"command.py: capability_unavailable — {exc.message} No command was executed.", file=sys.stderr)
        return 3
    except PermissionError as exc:
        print(f"command.py: authority denied — {exc}", file=sys.stderr)
        return 3
    except (ScriptUsageError, ValueError, OSError, RuntimeError):
        print("command.py: owner initialisation failed", file=sys.stderr)
        return 4
    finally:
        if seed_attachment is not None:
            seed_attachment.close()


def _run(argv, *, stdin, stdout, stderr, context_factory, script_path, owner_attachment,
         transport_identity, owner_initialisation_allowed, result_locks) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        catalogue = current_application_catalogue()
        resolver = current_request_resolver()
    except Exception:
        _report_internal_failure("command catalogue failed to load", stderr)
        return 4
    try:
        args = build_parser().parse_args(argv)
        if args.operation is not None and (not args.operation.strip() or len(args.operation.encode("utf-8")) > 128):
            raise ScriptUsageError("--operation requires an ID of 1–128 UTF-8 bytes")
        if args.noun == "workspace" and args.verb in {"setup", "ensure-registration", "bind"}:
            raise ScriptUsageError(
                "Retired workspace command; use workspace create, workspace register or workspace rebind"
            )
        command_id = command_id_from_argv(
            args.noun,
            args.verb,
            direct_script_command_ids(catalogue),
        )
        payload = _request_payload(args.request_json, stdin)
        if "brain_session" in payload:
            raise ScriptUsageError("Use --result-session-file for result-session context.")
        result_session = (_read_result_session(Path(args.result_session_file).expanduser().absolute(), command_id)
                          if args.result_session_file is not None else None)
    except (ScriptUsageError, ValueError, json.JSONDecodeError) as exc:
        print(f"command.py: invalid request — {exc}", file=stderr)
        return 2
    try:
        entry = next(item for item in catalogue.entries if item.command_id == command_id)
        session_path = Path(args.result_session_file).expanduser().absolute() if args.result_session_file is not None else None
        automatic_session = (session_path is None and entry.retain_output
                             and command_id not in RESULT_STORAGE_OPTIONAL_COMMANDS
                             and not command_id.startswith("result."))
        if (command_id.startswith("result.") and result_session is None
                and (owner_attachment is None or owner_attachment.kind != "cli-job")):
            raise InvalidResultSession("Select an existing result session before navigation or cleanup.")
        vault_root = resolve_direct_vault(args.vault, script_path=script_path)
        if automatic_session:
            if owner_attachment is not None and owner_attachment.kind == "cli-job":
                session_path = _automatic_result_session_path(vault_root, owner_attachment, result_locks)
                if session_path.exists() or session_path.is_symlink():
                    result_session = _read_result_session(session_path, command_id)
            else:
                _result_client_directory(vault_root, create=False)
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
            maintenance_invoker=True,
            **({"result_session": result_session} if result_session is not None else {}),
            **({"operation_id": args.operation} if args.operation is not None else {}),
            **({"owner_attachment": owner_attachment} if owner_attachment is not None else {}),
            **({"transport_identity": transport_identity, "owner_initialisation_allowed": owner_initialisation_allowed}
               if transport_identity is not None else {}),
        )
    except WorkspaceCompatibilityError as exc:
        from _application.adapter import project_adapter_result

        entry = next(item for item in catalogue.entries if item.command_id == command_id)
        projection = project_adapter_result(exc.result(entry))
        envelope, concise = _guard_cli_result(json.loads(projection.json_text), projection.concise_text)
        print(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")) if args.json else concise,
              file=stdout if args.json else stderr)
        return projection.exit_code
    except DeliveryFailure as exc:
        entry = next(item for item in catalogue.entries if item.command_id == command_id)
        projection = project_delivery_failure(command_id, entry.command_version, exc)
        envelope = projection.structured_content
        if session_path is not None and result_session not in (None, "new"):
            envelope["result_session_file"] = str(session_path)
        envelope, concise = _guard_cli_result(envelope, projection.concise_text)
        print(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")) if args.json else concise,
              file=stdout if args.json else stderr)
        return projection.exit_code
    except InvalidResultSession:
        print("command.py: invalid request — invalid result session; select its private file or run session start --result-session-file <new-private-path>", file=stderr)
        return 2
    except ValueError:
        print("command.py: invalid request — invalid invocation context", file=stderr)
        return 2
    except DirectContextError as exc:
        if str(exc) == "direct workspace is not bound to the selected Brain":
            print("command.py: infrastructure — direct workspace is not bound to the selected Brain", file=stderr)
        else:
            print("command.py: infrastructure — invocation context unavailable", file=stderr)
        return 4
    except OSError:
        print("command.py: infrastructure — invocation context unavailable", file=stderr)
        return 4
    except Exception:
        _report_internal_failure("invocation context unavailable", stderr)
        return 4
    if automatic_session and session_path is None and getattr(context, "result_delivery", None) is not None:
        try:
            session_path = _automatic_result_session_path(vault_root, None, result_locks)
        except (OSError, ValueError):
            print("command.py: infrastructure — private result-client file unavailable", file=stderr)
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
        report_failure_safely(
            context,
            phase="direct-script.invoke",
            command_id=command_id,
            error=exc,
        )
        _report_internal_failure("direct command setup failed", stderr)
        return 4
    credential = getattr(projection, "result_session_credential", None)
    credential_file_ready = result_session not in (None, "new")
    credential_delivery_failed = False
    if credential is not None and not credential_file_ready:
        try:
            if session_path is None:
                session_path = _automatic_result_session_path(vault_root, None, result_locks)
            _write_result_session(session_path, credential)
            credential_file_ready = True
        except Exception:
            # Source execution is already final. Preserve its outcome and effects.
            credential_delivery_failed = True
            print("command.py: result-session credential delivery failed; do not rerun a mutation to recover output", file=stderr)
    elif result_session == "new":
        print("command.py: result-session credential issuance unavailable", file=stderr)
    envelope = json.loads(projection.json_text)
    if credential_delivery_failed:
        from dataclasses import asdict
        from _bootstrap.result_metadata import Delivery
        envelope.setdefault("delivery", asdict(Delivery())).update(
            status="unavailable", availability="unavailable", reason="result_session_unavailable",
            message=RESULT_SESSION_UNAVAILABLE_MESSAGE,
            continuation=None, outside_continuation=None,
        )
    if isinstance(envelope.get("delivery"), dict):
        from _bootstrap.result_metadata import compact_delivery
        envelope["delivery"] = compact_delivery(envelope["delivery"])
    delivery = envelope.get("delivery")
    recoverable = not projection.is_error or (isinstance(delivery, dict) and delivery.get("result_id"))
    expose_session_file = credential_file_ready and recoverable
    if expose_session_file:
        envelope["result_session_file"] = str(session_path)
    envelope, concise = _guard_cli_result(envelope, projection.concise_text)
    if args.json:
        print(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")), file=stdout)
    else:
        stream = stderr if projection.is_error else stdout
        print(concise, file=stream)
        if isinstance(envelope.get("delivery"), dict) and envelope["delivery"].get("reason") == "capacity_exceeded":
            print(envelope["delivery"]["message"], file=stream)
        if "result_session_file" in envelope:
            print(f"result_session_file: {envelope['result_session_file']}", file=stream)
    return projection.exit_code


def _request_payload(value: str, stdin: TextIO) -> dict[str, object]:
    raw = stdin.read() if value == "-" else value
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ScriptUsageError("--request-json must decode to an object")
    return decoded


def _report_internal_failure(message: str, stderr: TextIO) -> None:
    print(f"command.py: internal_error — {message}", file=stderr)


def _result_session_location(path: Path) -> Path:
    """Pin a caller's parent alias while keeping the credential leaf no-follow."""
    try:
        return path.parent.resolve(strict=True) / path.name
    except (OSError, RuntimeError):
        raise ScriptUsageError("Result-session parent must be an existing accessible directory.") from None


def _read_result_session(path: Path, command_id: str) -> str:
    """Read a bounded credential without following a leaf symlink or special file."""
    try:
        descriptor = private_files.open_private_file(_result_session_location(path))
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            if command_id == "session.start" and not path.is_symlink():
                return "new"
            raise ScriptUsageError("Result-session file is missing; create it with session start.") from None
        raise ScriptUsageError("Result-session file must be a private owned regular file and readable safely.") from None
    try:
        raw = os.read(descriptor, 513)
        try:
            credential = raw.decode("utf-8")
        except UnicodeError:
            raise ScriptUsageError("Invalid result-session credential file.") from None
        if not credential.strip() or credential == "new" or len(raw) > 512:
            raise ScriptUsageError("Invalid result-session credential file.")
        return credential
    finally:
        os.close(descriptor)


def _write_result_session(path: Path, credential: str) -> None:
    """Publish one-time issuance atomically without replacing an existing file."""
    if not isinstance(credential, str) or not credential.strip() or credential == "new" or len(credential.encode("utf-8")) > 512:
        raise ValueError("Invalid result-session issuance.")
    try:
        private_files.create_secret(_result_session_location(path), credential.encode("utf-8"))
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, "Result-session file already exists.") from None
        raise


def _automatic_result_session_path(root: Path, attachment, locks: ExitStack) -> Path:
    """Select a unique client file or a namespace proved by the private job channel."""
    directory = _result_client_directory(root, create=True)
    if attachment is not None and attachment.kind == "cli-job":
        identity = attachment.connect(root).identity
        key = hashlib.sha256(identity.context_id.encode("utf-8")).hexdigest()
        path = directory / ("job-" + key)
        locks.enter_context(exclusive_file_lock(directory / ("job-" + key + ".lock"),
                                               timeout=30, follow_symlinks=False, create_parent=False))
        return path
    client = directory / ("client-" + secrets.token_hex(16))
    private_files.create_dir(client)
    return client / "session"


def _result_client_directory(root: Path, *, create: bool) -> Path:
    """Validate existing ancestors before source execution; create only for delivery."""
    directory = Path(root)
    target = directory / ".brain/local/result-clients"
    for component in (".brain", "local", "result-clients"):
        directory = directory / component
        if create:
            try:
                private_files.create_dir(directory)
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
        try:
            info = directory.lstat()
        except FileNotFoundError:
            if not create:
                return target
            raise
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Unsafe result-client directory.")
    try:
        private_files.validate_existing_dir(directory)
    except OSError:
        raise ValueError("Result-client directory must be private and owned.") from None
    return directory


CLI_CANONICAL_BYTES = 8000
CLI_ENCODED_BYTES = 32768


def _cli_result_fits(envelope: dict, concise: str) -> bool:
    canonical = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    path_line = "\nresult_session_file: " + envelope["result_session_file"] if "result_session_file" in envelope else ""
    return (len(canonical.encode("utf-8")) <= CLI_CANONICAL_BYTES
            and len(json.dumps(envelope).encode("utf-8")) <= CLI_ENCODED_BYTES
            and len((concise + path_line + "\n").encode("utf-8")) <= CLI_ENCODED_BYTES)


def _guard_cli_result(envelope: dict, concise: str) -> tuple[dict, str]:
    """Check final metadata and both CLI encodings without rewriting source execution."""
    envelope = dict(envelope)
    # CLI issuance has a protected file channel, never an inline credential control.
    envelope.pop("result_session", None)
    if _cli_result_fits(envelope, concise):
        return envelope, concise
    message = "Output exceeds the CLI delivery budget. Where retained, call result.read with result_id and detail=@outcome. Do not rerun a mutation."
    value = {key: envelope[key] for key in (
        "schema", "command", "command_version", "status", "committed_effects",
        "committed_effect_summary", "outcome_reference", "outcome", "result_ref", "result_session_file",
    ) if key in envelope}
    value["result"] = None
    from _bootstrap.result_outcome import source_outcome_warnings
    value["warnings"] = source_outcome_warnings(envelope.get("warnings"))
    if envelope.get("status") == "partial" and isinstance(envelope.get("result"), dict):
        if "committed_effects" in envelope["result"]:
            value["committed_effects"] = envelope["result"]["committed_effects"]
    if isinstance(envelope.get("error"), dict):
        value["error"] = {key: envelope["error"][key] for key in (
            "code", "effects", "retryable", "outcome_reference") if key in envelope["error"]}
        value["error"]["message"] = message
    original_delivery = envelope.get("delivery")
    original_delivery = original_delivery if isinstance(original_delivery, dict) else {}
    reason = "result_unavailable"
    capacity_limits = None
    if original_delivery.get("reason") == "capacity_exceeded":
        from _bootstrap.result_capacity import capacity_recovery
        capacity_limits, message = capacity_recovery(original_delivery.get("capacity_limits"))
        reason = "capacity_exceeded"
        message += " Do not rerun a mutation to recover output."
    elif original_delivery.get("reason") == "representation_too_large":
        from _bootstrap.result_capacity import representation_recovery
        reason, message = "representation_too_large", representation_recovery()
    elif original_delivery.get("reason") == "result_session_unavailable":
        reason, message = "result_session_unavailable", RESULT_SESSION_UNAVAILABLE_MESSAGE
    from _application.result_navigation import Delivery, delivery_metadata
    value["delivery"] = delivery_metadata(Delivery(status="unavailable",
        reason=reason, message=message))
    if capacity_limits is not None:
        value["delivery"]["capacity_limits"] = capacity_limits
    for key in ("result_id", "view_id"):
        if key in original_delivery:
            value["delivery"][key] = original_delivery[key]
    if not _cli_result_fits(value, message) and "committed_effects" in value:
        effects = value.pop("committed_effects")
        value["committed_effect_summary"] = {
            "count": len(effects) if isinstance(effects, (list, dict)) else None,
            "sha256": hashlib.sha256(json.dumps(effects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "detail": "Where retained, call result.read with result_id and detail=@outcome.",
        }
    optional = ("outcome", "result_ref", "outcome_reference", "result_session_file")
    for key in sorted((key for key in optional if key in value),
                      key=lambda key: len(json.dumps(value[key])), reverse=True):
        if _cli_result_fits(value, message):
            break
        value.pop(key)
        value.setdefault("metadata_unavailable", []).append(key)
    for key in ("result_id", "view_id"):
        if _cli_result_fits(value, message):
            break
        value["delivery"][key] = None
        value.setdefault("metadata_unavailable", []).append("delivery." + key)
    return value, message
