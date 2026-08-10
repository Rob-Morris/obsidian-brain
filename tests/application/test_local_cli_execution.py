"""Owner-preserving execution for the staged composed local CLI."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))


from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.adapter import LauncherAdapter, LauncherRequestError, project_launcher_result
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import (
    CommandError,
    CommittedEffect,
    Error,
    ErrorCode,
    Ok,
    Partial,
)
from _launcher.owners import LAUNCHER_OWNERS
from _local_cli.discovery import ComposedCommandEntry
from _local_cli.execution import (
    ApplicationProcessInvoker,
    LauncherCommandInvoker,
    LocalCliExecution,
    SelectedBrainProcess,
    render_local_result,
)
from _application.projection import canonical_result_envelope
from _application.receipts import CommittedEffect as ApplicationCommittedEffect
from _application.results import (
    CommandError as ApplicationCommandError,
    Error as ApplicationError,
    ErrorCode as ApplicationErrorCode,
    Ok as ApplicationOk,
    Partial as ApplicationPartial,
)


NOW = datetime.fromisoformat("2026-08-10T09:30:00+10:00")


class _Authority:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def allows(self, **kwargs):
        self.calls.append(kwargs)
        return self.allowed


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _context(tmp_path, *, authority=None):
    return LauncherContext(
        profile="reader",
        authority=authority or _Authority(),
        providers=ProviderBindings(),
        correlation_id="corr-local-cli",
        invocation_id="inv-local-cli",
        receipt_writer=_Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=tmp_path.resolve(),
        cli_version="1.2.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
    )


def _entry(owner, command_id, version=1):
    return ComposedCommandEntry(
        owner,
        f"brain.{'command' if owner == 'application' else 'launcher'}-catalogue/1",
        f"sha256:{owner}",
        command_id,
        version,
        "Test one command.",
        {"command_id": command_id, "command_version": version},
    )


def _launcher_adapter():
    return LauncherAdapter(LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def test_launcher_dynamic_adapter_denies_before_payload_resolution(tmp_path):
    authority = _Authority(allowed=False)

    result = _launcher_adapter().invoke(
        _context(tmp_path, authority=authority),
        "brain.version",
        {"unknown": "payload must remain undecoded"},
    )

    assert result.result.error.code is ErrorCode.AUTHORITY_DENIED
    assert result.exit_code == 3
    assert authority.calls == [
        {"command_id": "brain.version", "required": "reader", "effect": "none"}
    ]


def test_launcher_dynamic_adapter_rejects_unknown_identity_and_fields(tmp_path):
    adapter = _launcher_adapter()

    with pytest.raises(LauncherRequestError, match="not owned"):
        adapter.invoke(_context(tmp_path), "artefact.read", {})
    with pytest.raises(LauncherRequestError, match="unknown fields"):
        adapter.invoke(_context(tmp_path), "brain.version", {"extra": True})


def test_launcher_projection_matches_application_structural_wire_vocabulary():
    pairs = (
        (
            Ok("brain.version", 1, {"value": "1.2.0"}),
            ApplicationOk("brain.version", 1, {"value": "1.2.0"}),
        ),
        (
            Partial(
                "brain.version",
                1,
                CommandError(ErrorCode.CONFLICT, "Partially changed."),
                (CommittedEffect("changed", "machine"),),
            ),
            ApplicationPartial(
                "brain.version",
                1,
                ApplicationCommandError(
                    ApplicationErrorCode.CONFLICT,
                    "Partially changed.",
                ),
                (ApplicationCommittedEffect("changed", "machine"),),
            ),
        ),
        (
            Error(
                "brain.version",
                1,
                CommandError(ErrorCode.CAPABILITY_UNAVAILABLE, "Unavailable."),
            ),
            ApplicationError(
                "brain.version",
                1,
                ApplicationCommandError(
                    ApplicationErrorCode.CAPABILITY_UNAVAILABLE,
                    "Unavailable.",
                ),
            ),
        ),
    )

    for launcher_result, application_result in pairs:
        launcher = project_launcher_result(launcher_result)
        application = canonical_result_envelope(application_result)

        assert launcher.structured_content == application
        assert json.loads(launcher.json_text) == application
    assert project_launcher_result(pairs[0][0]).concise_text == "brain.version: ok"
    assert project_launcher_result(pairs[0][0]).exit_code == 0


def test_application_process_invoker_executes_the_selected_brain_command(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    script = vault / ".brain-core" / "scripts" / "command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")
    calls = []
    envelope = {
        "schema": "brain.command-result/1",
        "command": "artefact.read",
        "command_version": 1,
        "status": "ok",
        "warnings": [],
        "result": {"path": "Designs/Test.md"},
        "committed_effects": [],
    }

    def runner(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, json.dumps(envelope) + "\n", "")

    target = SelectedBrainProcess(
        vault,
        Path(sys.executable).resolve(),
        workspace=tmp_path.resolve(),
        operator_key="amber-river-crown",
        dry_run=True,
    )
    result = ApplicationProcessInvoker(target, runner).invoke(
        _entry("application", "artefact.read"),
        {"path": "Designs/Test.md"},
    )

    argv, options = calls[0]
    assert argv[:4] == [str(target.python), str(script), "artefact", "read"]
    assert json.loads(argv[argv.index("--request-json") + 1]) == {
        "path": "Designs/Test.md"
    }
    assert argv[argv.index("--vault") + 1] == str(vault)
    assert argv[argv.index("--workspace") + 1] == str(tmp_path.resolve())
    assert argv[argv.index("--operator-key") + 1] == "amber-river-crown"
    assert "--dry-run" in argv and "--json" in argv
    assert options == {"capture_output": True, "text": True, "check": False}
    assert result.owner == "application"
    assert result.structured_content == envelope
    assert result.concise_text == "artefact.read: ok"


def test_local_execution_routes_each_owner_without_semantic_import_merging(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    script = vault / ".brain-core" / "scripts" / "command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")

    def runner(argv, **_options):
        envelope = {
            "schema": "brain.command-result/1",
            "command": "artefact.read",
            "command_version": 1,
            "status": "ok",
            "warnings": [],
            "result": {},
            "committed_effects": [],
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(envelope), "")

    execution = LocalCliExecution(
        (
            ApplicationProcessInvoker(
                SelectedBrainProcess(vault, Path(sys.executable).resolve()),
                runner,
            ),
            LauncherCommandInvoker(_context(tmp_path), _launcher_adapter()),
        )
    )

    application = execution.invoke(_entry("application", "artefact.read"), {})
    launcher = execution.invoke(_entry("launcher", "brain.version"), {})

    assert application.owner == "application"
    assert launcher.owner == "launcher"
    assert launcher.structured_content["command"] == "brain.version"
    assert launcher.exit_code == 0


def test_selected_brain_process_rejects_ambiguous_roots(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    vault.mkdir()
    link = tmp_path / "Brain-link"
    link.symlink_to(vault, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        SelectedBrainProcess(link, Path(sys.executable).resolve())
    with pytest.raises(ValueError, match="absolute"):
        SelectedBrainProcess(Path("relative"), Path(sys.executable).resolve())


def test_application_process_invoker_rejects_malformed_child_contract(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    script = vault / ".brain-core" / "scripts" / "command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")

    def runner(argv, **_options):
        return subprocess.CompletedProcess(argv, 0, '{"status":"ok"}\n', "")

    invoker = ApplicationProcessInvoker(
        SelectedBrainProcess(vault, Path(sys.executable).resolve()),
        runner,
    )

    with pytest.raises(RuntimeError, match="schema"):
        invoker.invoke(_entry("application", "artefact.read"), {})


def test_application_process_invoker_rejects_exit_category_drift(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    script = vault / ".brain-core" / "scripts" / "command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")
    envelope = {
        "schema": "brain.command-result/1",
        "command": "artefact.read",
        "command_version": 1,
        "status": "error",
        "warnings": [],
        "result": None,
        "error": {
            "code": "authority_denied",
            "message": "Denied.",
            "details": None,
            "next_action": None,
            "effects": "none",
            "retryable": False,
        },
    }

    def runner(argv, **_options):
        return subprocess.CompletedProcess(argv, 2, json.dumps(envelope), "")

    invoker = ApplicationProcessInvoker(
        SelectedBrainProcess(vault, Path(sys.executable).resolve()),
        runner,
    )

    with pytest.raises(RuntimeError, match="exit category"):
        invoker.invoke(_entry("application", "artefact.read"), {})


def test_launcher_projection_error_uses_the_same_exit_categories():
    result = project_launcher_result(
        Error(
            "brain.version",
            1,
            error=CommandError(ErrorCode.CAPABILITY_UNAVAILABLE, "Unavailable."),
        )
    )

    assert result.exit_code == 3
    assert result.structured_content["error"]["effects"] == "none"


def test_local_result_renderer_exclusively_owns_json_and_human_streams(tmp_path):
    adapter = _launcher_adapter()
    ok = LauncherCommandInvoker(_context(tmp_path), adapter).invoke(
        _entry("launcher", "brain.version"),
        {},
    )
    denied = LauncherCommandInvoker(
        _context(tmp_path, authority=_Authority(allowed=False)),
        adapter,
    ).invoke(_entry("launcher", "brain.version"), {})

    assert render_local_result(ok, json_mode=True) == (ok.json_text + "\n", "", 0)
    assert render_local_result(ok, json_mode=False) == (
        "brain.version: ok\n",
        "",
        0,
    )
    assert render_local_result(denied, json_mode=True) == (
        denied.json_text + "\n",
        "",
        3,
    )
    assert render_local_result(denied, json_mode=False) == (
        "",
        denied.concise_text + "\n",
        3,
    )
