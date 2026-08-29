"""Direct noun/verb command adapter tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from io import StringIO
import json

from _application.receipts import MemoryReceiptStore
from _application.registry import current_application_catalogue
from _application.types import Availability, DependencyTier, SnapshotFreshness
from _command_interface.context import compose_local_context
import _command_interface.direct as direct_context
import _command_interface.script as direct_script
from _command_interface.direct import resolve_direct_vault
from _command_interface.script import run


NOW = datetime.fromisoformat("2026-08-10T10:00:00+10:00")


class _Clock:
    def now(self):
        return NOW


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    (root / ".brain-core").mkdir(parents=True, exist_ok=True)
    (root / ".brain-core" / "VERSION").write_text("0.54.48\n")
    return root


def _context_factory(tmp_path, tools):
    counter = 0

    def create(**options):
        nonlocal counter
        counter += 1
        clock = _Clock()
        return compose_local_context(
            vault_root=options["vault_root"],
            brain_id="test-brain",
            profile="reader",
            allowed_tools=frozenset(tools),
            dependency_tier=DependencyTier.PORTABLE,
            provider_ids=(),
            capability_states=(),
            snapshot_token=f"snapshot-{counter}",
            snapshot_freshness=SnapshotFreshness.FRESH,
            snapshot_observed_at=NOW,
            correlation_id=f"corr-{counter}",
            invocation_id=f"inv-{counter}",
            receipt_store=MemoryReceiptStore(clock),
            dry_run=options["dry_run"],
            clock=clock,
        )

    return create


def _run(tmp_path, argv, *, tools=("command.list",), stdin=""):
    out = StringIO()
    err = StringIO()
    code = run(
        [*argv, "--vault", str(_vault(tmp_path))],
        stdin=StringIO(stdin),
        stdout=out,
        stderr=err,
        context_factory=_context_factory(tmp_path, tools),
    )
    return code, out.getvalue(), err.getvalue()


def _unexpected_probe(name):
    def fail(*_args):
        raise AssertionError(f"default command list probed {name}")

    return fail


def test_direct_json_stdout_is_only_the_canonical_envelope(tmp_path):
    code, stdout, stderr = _run(
        tmp_path,
        ["command", "list", "--request-json", '{"page_size":1}', "--json"],
    )

    payload = json.loads(stdout)
    assert code == 0
    assert stderr == ""
    assert payload["schema"] == "brain.command-result/1"
    assert payload["command"] == "command.list"
    assert payload["status"] == "ok"


def test_direct_pre_context_failure_is_privacy_bounded(monkeypatch):
    def fail():
        raise RuntimeError("catalogue diagnostic")

    monkeypatch.setattr(direct_script, "current_application_catalogue", fail)
    stderr = StringIO()

    code = run([], stdout=StringIO(), stderr=stderr)

    assert code == 4
    assert "command catalogue failed to load" in stderr.getvalue()
    assert "catalogue diagnostic" not in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_direct_invoke_failure_reports_trusted_diagnostic_without_public_detail(
    tmp_path,
    monkeypatch,
):
    failures = []

    class Diagnostics:
        def report_failure(self, **failure):
            failures.append(failure)

    base_factory = _context_factory(tmp_path, ("command.list",))

    def context_factory(**options):
        return replace(base_factory(**options), diagnostics=Diagnostics())

    def fail_invoke(*_args, **_kwargs):
        raise RuntimeError("private vault/provider detail")

    monkeypatch.setattr(direct_script.ApplicationAdapter, "invoke", fail_invoke)
    stderr = StringIO()

    code = run(
        ["command", "list", "--vault", str(_vault(tmp_path))],
        stdout=StringIO(),
        stderr=stderr,
        context_factory=context_factory,
    )

    assert code == 4
    assert stderr.getvalue() == (
        "command.py: internal_error — direct command setup failed\n"
    )
    assert failures[0]["phase"] == "direct-script.invoke"
    assert failures[0]["command_id"] == "command.list"
    assert failures[0]["correlation_id"] == "corr-1"
    assert str(failures[0]["error"]) == "private vault/provider detail"


def test_direct_human_output_and_authority_exit_are_structural(tmp_path):
    ok = _run(tmp_path, ["command", "list", "--request-json", '{"page_size":1}'])
    denied = _run(
        tmp_path,
        ["artefact", "list"],
        tools=("command.list",),
    )

    assert ok == (0, "command.list: ok\n", "")
    assert denied[0] == 3
    assert denied[1] == ""
    assert "authority_denied" in denied[2]


def test_direct_request_errors_use_exit_two_without_executor_entry(tmp_path):
    malformed = _run(tmp_path, ["command", "list", "--request-json", "[]"])
    unknown = _run(tmp_path, ["not-a", "command"])
    wrong_fields = _run(
        tmp_path,
        ["command", "list", "--request-json", '{"unknown":true}'],
    )

    assert malformed[0] == unknown[0] == wrong_fields[0] == 2
    assert malformed[1] == unknown[1] == wrong_fields[1] == ""
    assert "invalid request" in malformed[2]
    assert "invalid request" in unknown[2]
    assert "invalid_request" in wrong_fields[2]


def test_direct_request_can_be_read_from_stdin(tmp_path):
    code, stdout, stderr = _run(
        tmp_path,
        ["command", "list", "--request-json", "-", "--json"],
        stdin='{"page_size":1}',
    )

    assert code == 0
    assert json.loads(stdout)["command"] == "command.list"
    assert stderr == ""


def test_direct_json_projects_known_request_failures_structurally(tmp_path):
    code, stdout, stderr = _run(
        tmp_path,
        ["command", "list", "--request-json", '{"unknown":true}', "--json"],
    )

    payload = json.loads(stdout)
    assert code == 2
    assert stderr == ""
    assert payload["command"] == "command.list"
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["details"]["reason"].startswith(
        "invalid request for command.list"
    )


def test_direct_vault_resolution_is_explicit_and_non_exiting(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setenv("BRAIN_VAULT_ROOT", str(vault))

    assert resolve_direct_vault(None) == vault
    try:
        resolve_direct_vault(str(tmp_path / "missing"))
    except RuntimeError as exc:
        assert "not an installed Brain vault" in str(exc)
    else:
        raise AssertionError("invalid direct vault unexpectedly resolved")


def test_direct_command_observes_real_local_composition(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    (vault / ".brain").mkdir()
    (vault / ".brain" / "config.yaml").write_text(
        "vault:\n"
        "  profiles:\n"
        "    operator:\n"
        "      allow: [command.list]\n"
        "defaults:\n"
        "  default_profile: operator\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setattr(
        direct_context,
        "_probe_obsidian_cli",
        _unexpected_probe("Obsidian"),
    )
    monkeypatch.setattr(
        direct_context,
        "_probe_semantic_retrieval",
        _unexpected_probe("semantic retrieval"),
    )
    monkeypatch.setattr(
        direct_context,
        "_managed_provider_availability",
        _unexpected_probe("managed providers"),
    )
    stdout = StringIO()
    stderr = StringIO()

    code = run(
        ["command", "list", "--vault", str(vault), "--json"],
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["command"] == "command.list"
    assert stderr.getvalue() == ""
    assert not (vault / ".brain" / "local" / "command-outcomes").exists()


def test_direct_context_accepts_selected_vault_as_project_anchor(
    tmp_path,
    monkeypatch,
):
    vault = _vault(tmp_path)
    (vault / ".brain").mkdir()
    (vault / ".brain" / "config.yaml").write_text(
        "vault:\n"
        "  profiles:\n"
        "    operator:\n"
        "      allow: [runtime.read-environment]\n"
        "defaults:\n"
        "  default_profile: operator\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    context = direct_context.compose_direct_context(
        vault_root=vault,
        command_id="runtime.read-environment",
        catalogue=current_application_catalogue(),
        workspace_dir=vault,
        invocation_id="mcp-vault-self-project",
        clock=_Clock(),
    )

    assert context.selected_brain.vault_root == vault
    assert context.workspace_dir == vault


def test_direct_provider_inventory_is_complete_and_refresh_is_deduplicated(
    tmp_path,
    monkeypatch,
):
    catalogue = current_application_catalogue()
    declared = {
        provider
        for entry in catalogue.entries
        for provider in (*entry.required_providers, *entry.optional_providers)
    }
    assert declared == set(direct_context._LOCAL_PROVIDERS)

    vault = _vault(tmp_path)
    (vault / ".brain").mkdir()
    (vault / ".brain" / "config.yaml").write_text(
        "vault:\n"
        "  profiles:\n"
        "    operator:\n"
        "      allow: [command.list]\n"
        "defaults:\n"
        "  default_profile: operator\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    calls = {"obsidian": 0, "semantic": 0, "managed": 0}

    def probe(name, state):
        def invoke(*_args):
            calls[name] += 1
            return state

        return invoke

    monkeypatch.setattr(
        direct_context,
        "_probe_obsidian_cli",
        probe("obsidian", Availability.UNAVAILABLE),
    )
    monkeypatch.setattr(
        direct_context,
        "_probe_semantic_retrieval",
        probe("semantic", Availability.UNAVAILABLE),
    )
    monkeypatch.setattr(
        direct_context,
        "_managed_provider_availability",
        probe("managed", Availability.UNAVAILABLE),
    )
    stdout = StringIO()

    code = run(
        [
            "command",
            "list",
            "--vault",
            str(vault),
            "--request-json",
            '{"refresh":true,"page_size":200}',
            "--json",
        ],
        stdout=stdout,
        stderr=StringIO(),
    )

    assert code == 0
    assert json.loads(stdout.getvalue())["result"]["availability_freshness"] == "fresh"
    assert calls == {"obsidian": 1, "semantic": 1, "managed": 2}
