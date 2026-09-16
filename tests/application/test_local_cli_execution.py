"""Owner-preserving execution for the staged composed local CLI."""

from __future__ import annotations

from datetime import datetime
from dataclasses import replace
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
from _launcher.invocation import LauncherInvocation
from _launcher.contracts import (
    CommandError,
    CommittedEffect,
    Error,
    ErrorCode,
    Ok,
    OutcomeReceipt,
    OutcomeReference,
    Partial,
    ReceiptState,
)
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.owners import LauncherOwners
from _launcher.version import BrainVersionRequest
from _local_cli.discovery import ComposedCommandEntry
from _local_cli.execution import (
    ApplicationProcessInvoker,
    LauncherCommandInvoker,
    LocalCliExecution,
    SelectedBrainProcess,
    render_local_result,
)
from _local_cli.main import CliError, _trusted_distribution, run
from _local_cli.runtime import (
    LauncherDiagnosticReporter,
    LauncherReceiptStore,
    SelectedBrain,
    command_python,
    resolve_project_exposure_brain,
    resolve_selected_brain,
)
from _common import _operational_log
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


def test_managed_command_python_preserves_virtual_environment_entry_point(
    tmp_path, monkeypatch
):
    vault = (tmp_path / "Brain").resolve()
    managed = tmp_path / "venv" / "bin" / "python"
    managed.parent.mkdir(parents=True)
    managed.symlink_to(Path(sys.executable).resolve())
    selected = SelectedBrain(vault, None, "vault_self")

    monkeypatch.setattr(
        "_common._venv.find_runnable_python",
        lambda *_args, **_kwargs: managed,
    )

    assert command_python(selected, "managed") == managed.absolute()
    assert command_python(selected, "managed") != managed.resolve()


def test_managed_command_python_executes_inside_selected_virtual_environment(
    tmp_path, monkeypatch
):
    vault = (tmp_path / "Brain").resolve()
    venv_dir = tmp_path / "managed-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    managed = (
        venv_dir / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else venv_dir / "bin" / "python"
    )
    site_packages = subprocess.run(
        [str(managed), "-c", "import site; print(site.getsitepackages()[0])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    Path(site_packages, "brain_cli_venv_marker.py").write_text(
        "VALUE = 'managed-runtime'\n",
        encoding="utf-8",
    )
    selected = SelectedBrain(vault, None, "vault_self")
    monkeypatch.setattr(
        "_common._venv.find_runnable_python",
        lambda *_args, **_kwargs: managed,
    )

    chosen = command_python(selected, "managed")
    probe = subprocess.run(
        [
            str(chosen),
            "-c",
            (
                "import json, sys, brain_cli_venv_marker; "
                "print(json.dumps({'prefix': sys.prefix, "
                "'marker': brain_cli_venv_marker.VALUE}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout)

    assert Path(payload["prefix"]).resolve() == venv_dir.resolve()
    assert payload["marker"] == "managed-runtime"


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


@pytest.mark.parametrize("diagnostics_writable", [True, False])
def test_real_launcher_failure_preserves_the_returned_correlation_id(
    tmp_path, monkeypatch, capsys, diagnostics_writable
):
    vault = (tmp_path / "Brain").resolve()
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.61.0\n", encoding="utf-8")
    if not diagnostics_writable:
        mkdir = Path.mkdir

        def deny_diagnostics(path, *args, **kwargs):
            if path == vault / _operational_log.DIAGNOSTICS_REL:
                raise PermissionError("private diagnostics path")
            return mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", deny_diagnostics)
    context = replace(
        _context(tmp_path),
        current_vault=vault,
        diagnostics=LauncherDiagnosticReporter(vault),
    )

    def _fail(_context, _request):
        raise RuntimeError("private failure detail")

    owners = LauncherOwners(
        tuple(
            replace(owner, executor=_fail)
            if owner.command_id == "brain.version"
            else owner
            for owner in LAUNCHER_OWNERS.entries
        )
    )
    result = LauncherInvocation(context, LAUNCHER_CATALOGUE, owners).invoke(
        BrainVersionRequest()
    )

    assert result.error.details.correlation_id == "corr-local-cli"
    assert result.error.code is ErrorCode.INTERNAL_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    if diagnostics_writable:
        assert captured.err == ""
        records = [
            json.loads(line)
            for line in (
                _operational_log.diagnostics_directory(vault) / "command.log"
            ).read_text(encoding="utf-8").splitlines()
        ]
    else:
        assert "private" not in captured.err
        assert str(vault) not in captured.err
        records = [json.loads(captured.err.splitlines()[-1].removeprefix("[brain-diagnostics] "))]
    assert records[-1]["event"] == "command.failed"
    assert records[-1]["phase"] == "execute"
    assert records[-1]["command_id"] == "brain.version"
    assert records[-1]["correlation_id"] == result.error.details.correlation_id
    assert records[-1]["exception_type"] == "RuntimeError"
    assert records[-1]["error_class"] == "internal"
    assert "private failure detail" not in json.dumps(records[-1])


@pytest.mark.parametrize("deny_writes", [False, True])
def test_runtime_inspect_does_not_need_machine_receipt_storage(
    tmp_path, monkeypatch, capsys, deny_writes
):
    vault = tmp_path / "external-brain"
    core = vault / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.62.0\n", encoding="utf-8")
    requirements = core / "brain_mcp"
    requirements.mkdir()
    for name in ("requirements.txt", "requirements-semantic.txt"):
        (requirements / name).write_text("# test runtime\n", encoding="utf-8")
    home = tmp_path / "home"
    state = home / ".local" / "state"
    caller = tmp_path / "caller-workspace"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(REPO_ROOT / "cli" / "brain"))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(REPO_ROOT))
    mkdir = Path.mkdir
    attempts = []

    def deny_machine_state(path, *args, **kwargs):
        if path == state or state in path.parents:
            attempts.append(path)
            if deny_writes:
                raise PermissionError("machine state is outside the sandbox")
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_machine_state)

    code = run(["runtime", "inspect", "--vault", str(vault), "--json"])

    captured = capsys.readouterr()
    assert code == 0, captured
    assert json.loads(captured.out)["status"] == "ok"
    assert captured.err == ""
    assert attempts == []
    assert not state.exists()


@pytest.mark.parametrize(
    "state", [ReceiptState.COMMITTED, ReceiptState.KNOWN_PARTIAL, ReceiptState.UNKNOWN]
)
def test_launcher_effect_receipts_remain_durable_and_write_failures_propagate(
    tmp_path, monkeypatch, state
):
    root = tmp_path / "command-outcomes"
    store = LauncherReceiptStore(root)
    effects = () if state is ReceiptState.UNKNOWN else (CommittedEffect("changed", "machine"),)
    receipt = OutcomeReceipt(
        OutcomeReference("cli-receipt"), "runtime.repair", 1, state, NOW, effects
    )
    store.write(receipt)
    payload = json.loads((root / "cli-receipt.json").read_text(encoding="utf-8"))
    assert payload["state"] == state.value
    assert payload["committed_effects"] == [
        {"kind": effect.kind, "subject": effect.subject} for effect in effects
    ]

    def deny_mkdir(*_args, **_kwargs):
        raise PermissionError("receipt storage is outside the sandbox")

    monkeypatch.setattr(Path, "mkdir", deny_mkdir)
    with pytest.raises(PermissionError):
        store.write(receipt)


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


@pytest.mark.parametrize(
    "child_stderr", ["", "native runtime warning\nprivate diagnostic\n"]
)
def test_application_process_invoker_executes_the_selected_brain_command(
    tmp_path, child_stderr
):
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
        return subprocess.CompletedProcess(
            argv, 0, json.dumps(envelope) + "\n", child_stderr
        )

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


def _application_failure_projection(tmp_path, stderr, *, error_code="internal_error", correlation_id="corr-child"):
    vault = (tmp_path / "Brain").resolve()
    script = vault / ".brain-core/scripts/command.py"
    script.parent.mkdir(parents=True)
    script.write_text("# selected Brain command owner\n", encoding="utf-8")
    envelope = {
        "schema": "brain.command-result/1", "command": "runtime.status",
        "command_version": 1, "status": "error", "warnings": [],
        "error": {"code": error_code, "message": "Command failed.",
                  "details": {"correlation_id": correlation_id}},
        "effects": "none",
    }
    def runner(argv, **_options):
        return subprocess.CompletedProcess(argv, 4, json.dumps(envelope), stderr)
    return ApplicationProcessInvoker(
        SelectedBrainProcess(vault, Path(sys.executable).resolve()), runner,
    ).invoke(_entry("application", "runtime.status"), {})


def _child_failure_record():
    return json.loads(_operational_log.encode_record(
        process="script", run_id="run-child", seq=1, event="command.failed",
        phase="execute", command_id="runtime.status", correlation_id="corr-child",
        error_class="io", exception_type="PermissionError",
    ))


@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_renders_only_matching_bounded_child_failure(tmp_path, json_mode):
    record = _child_failure_record()
    line = "[brain-diagnostics] " + json.dumps(record)
    projection = _application_failure_projection(
        tmp_path, "private runtime warning at /secret/path\n" + line + "\n" + line + "\n",
    )

    stdout, stderr, code = render_local_result(projection, json_mode=json_mode)

    assert code == 4
    assert "private" not in stderr and "/secret" not in stderr
    assert stderr.count("[brain-diagnostics]") == 1
    assert json.loads(stderr.splitlines()[-1].removeprefix("[brain-diagnostics] ")) == record
    assert len(stderr.splitlines()[-1].encode()) <= _operational_log.MAX_RECORD_BYTES + len("[brain-diagnostics] ")
    if json_mode:
        assert json.loads(stdout) == projection.structured_content
    else:
        assert stdout == ""
        assert stderr.startswith(projection.concise_text + "\n")


@pytest.mark.parametrize("changes", [
    {"schema": "brain.debug-bodies/1"}, {"event": "tool.handled"},
    {"process": "cli"}, {"command_id": "artefact.read"},
    {"correlation_id": "corr-other"}, {"phase": "private phase /secret"},
    {"exception_type": "PermissionError: /secret/path"}, {"error_class": "private"},
    {"message": "private exception text"}, {"ts": "private"}, {"pid": True},
    {"seq": -1}, {"run_id": "/secret/path"}, {"version": "/private/0.1.0"},
    {"dropped_before": "private"}, {"command_id": None}, {"phase": None},
])
def test_cli_rejects_mismatched_or_non_content_free_child_records(tmp_path, changes):
    record = {**_child_failure_record(), **changes}
    projection = _application_failure_projection(tmp_path, "[brain-diagnostics] " + json.dumps(record))

    assert render_local_result(projection, json_mode=True)[1] == ""


@pytest.mark.parametrize("stderr", [
    "Traceback: private exception /secret/path\n", "[brain-diagnostics] not JSON",
    "[brain-diagnostics] []", "[brain-diagnostics] {}",
    "[brain-diagnostics] operational log append failed: PermissionError\n",
    "[brain-diagnostics] " + " " * _operational_log.MAX_RECORD_BYTES + "{}",
    "[brain-diagnostics] " + "[" * 1100,
])
def test_cli_rejects_arbitrary_malformed_or_oversized_child_stderr(tmp_path, stderr):
    projection = _application_failure_projection(tmp_path, stderr)

    assert render_local_result(projection, json_mode=True)[1] == ""


@pytest.mark.parametrize("options", [
    {"correlation_id": None}, {"correlation_id": "different"},
    {"error_code": "command_outcome_unknown"},
])
def test_cli_child_failure_requires_matching_internal_error_envelope(tmp_path, options):
    stderr = "[brain-diagnostics] " + json.dumps(_child_failure_record())
    projection = _application_failure_projection(tmp_path, stderr, **options)

    assert render_local_result(projection, json_mode=True)[1] == ""


def test_selected_brain_process_rejects_ambiguous_roots(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    vault.mkdir()
    link = tmp_path / "Brain-link"
    link.symlink_to(vault, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        SelectedBrainProcess(link, Path(sys.executable).resolve())
    with pytest.raises(ValueError, match="absolute"):
        SelectedBrainProcess(Path("relative"), Path(sys.executable).resolve())


def test_local_cli_rejects_symlinked_selected_brain(tmp_path):
    vault = tmp_path / "Brain"
    core = vault / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.55.0\n", encoding="utf-8")
    link = tmp_path / "Brain-link"
    link.symlink_to(vault, target_is_directory=True)

    with pytest.raises(ValueError, match="not an installed local Brain"):
        resolve_selected_brain(vault=str(link), brain_id=None, workspace=None)


def test_real_local_cli_project_exposure_uses_default_and_preserves_project(
    tmp_path,
    monkeypatch,
):
    import vault_registry

    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    vault = tmp_path / "Brain"
    skill = vault / ".brain-core/skills/example"
    skill.mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.62.0\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example\n---\n\nExample.\n",
        encoding="utf-8",
    )
    binary = tmp_path / "brain-cli"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(binary))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(REPO_ROOT))
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    vault_registry.register(str(vault), "default-brain")
    vault_registry.set_default("default-brain")

    selected = resolve_project_exposure_brain(
        vault=None,
        brain_id=None,
        workspace=str(project),
    )
    code = run(
        [
            "--workspace",
            str(project),
            "--request-json",
            json.dumps({"name": "example", "client": "codex", "scope": "project"}),
            "--json",
            "skill",
            "expose",
        ]
    )

    assert selected.vault_root == vault.resolve()
    assert selected.workspace == project.resolve()
    assert code == 0
    assert (project / ".codex/skills/example/SKILL.md").is_file()
    assert not (project / ".brain/local/workspace.yaml").exists()

    monkeypatch.chdir(project)
    removed = run(
        [
            "--request-json",
            json.dumps({"name": "example", "client": "codex", "scope": "project"}),
            "--json",
            "skill",
            "unexpose",
        ]
    )
    assert removed == 0
    assert not (project / ".codex/skills/example").exists()


def test_local_cli_rejects_symlinked_distribution_identity(tmp_path, monkeypatch):
    binary = tmp_path / "brain"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    distribution = tmp_path / "distribution"
    catalogue = distribution / "cli" / "launcher_catalogue.py"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text("# catalogue\n", encoding="utf-8")
    linked_distribution = tmp_path / "distribution-link"
    linked_distribution.symlink_to(distribution, target_is_directory=True)
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(binary))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(linked_distribution))

    with pytest.raises(CliError, match="distribution is missing or unsafe"):
        _trusted_distribution()


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


def test_cli_discovery_json_input_and_flat_page_preserve_continuation(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from _local_cli import main
    from _application.application import CommandApplication
    from _application.foundation import build_application_catalogue, build_request_resolver
    from test_foundation_commands import _context

    app = CommandApplication(_context(tmp_path), build_application_catalogue())
    resolver = build_request_resolver()
    seen = []

    def invoke(_selected, command_id, request, _common, **_kwargs):
        seen.append(request)
        return canonical_result_envelope(app.invoke(resolver.resolve(command_id, request)))

    monkeypatch.setattr(main, "_invoke_application", invoke)
    selected = SimpleNamespace(supports_command_interface=True)

    def page(request):
        common = SimpleNamespace(request_json=json.dumps(request), json_mode=True)
        assert main._run_discovery(["command", "list"], common=common, selected=selected,
                                   cli_binary=tmp_path / "brain", distribution_root=tmp_path) == 0
        return json.loads(capsys.readouterr().out)

    first = page({"owner": "application", "page_size": 2})
    assert len(first["entries"]) == 2
    assert first["schema"] == "brain.local-command-list/2"
    assert "application" in first["catalogues"]
    assert all("payload" not in item and "catalogue_fingerprint" not in item
               for item in first["entries"])
    second = page({"owner": "application", "page_size": 2,
                   "cursor": first["application_next_cursor"]})
    assert [item["command_id"] for item in second["entries"]] == ["invocation.read"]
    assert second["application_next_cursor"] is None
    assert seen[0]["page_size"] == 2
