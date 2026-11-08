"""Application effect routing and narrow policy recovery, without installed runtimes."""

from dataclasses import dataclass
import os
import subprocess
from typing import ClassVar

import pytest

from command_application import context_for
from _application.application import CommandApplication
from _application.branch_effects import BRANCH_EFFECTS, validate_branch_effect_coverage
from _application.catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationCatalogue, ApplicationEntry
from _application.preparation import LIVE_QUERY, OperationPreparation, admit_owner, live_query
from _application.receipts import CommittedEffect
from _application.registry import current_application_catalogue, current_request_resolver
from _application.projection import canonical_result_envelope, request_schema, result_payload_schema
from _application.results import Error, ErrorCode, Ok, Partial
from _application.types import Authority, BranchEffect, DependencyTier, EffectClass, InitialAuthorisationClass, Locality, RetryClass
from _application.vault import configure_branch_guard as configuration
from _bootstrap.branch_diagnostics import branch_guard_findings
from _bootstrap import branch_guard
from _bootstrap.local_config import read_local_config


def git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], env=env,
                          check=True, capture_output=True, text=True, timeout=5)


@pytest.fixture
def guarded_vault(tmp_path):
    git(tmp_path, "init", "-q", "-b", "dev")
    (tmp_path / ".brain/local").mkdir(parents=True)
    (tmp_path / ".brain/local/config.yaml").write_text("defaults:\n  custom: retained\n")
    branch_guard.configure_guard(tmp_path, ["dev", "other"], read_local_config(tmp_path).revision)
    return tmp_path


@dataclass(frozen=True)
class ProbePayload:
    value: str = "ok"


@dataclass(frozen=True)
class ProtectedProbe:
    COMMAND_ID: ClassVar[str] = "document.write-body"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ProbePayload


@dataclass(frozen=True)
class CacheProbe:
    COMMAND_ID: ClassVar[str] = "relationship.refresh"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ProbePayload


def application(root, request_type, executor, *, protected=True, **options):
    entry = ApplicationEntry(request_type, executor, DependencyTier.PORTABLE,
        Locality.SELECTED_BRAIN_LOCAL, (), (), Authority.MAINTAINER,
        EffectClass.SELECTED_BRAIN_MUTATION if protected else EffectClass.DERIVED_CACHE_WRITE,
        InitialAuthorisationClass.CONTENT if protected else InitialAuthorisationClass.OBSERVATION,
        RetryClass.RECEIPT_REQUIRED if protected else RetryClass.SAFE,
        ALL_APPLICATION_PROJECTIONS,
        preparation=OperationPreparation(live_query) if protected else LIVE_QUERY)
    catalogue = ApplicationCatalogue((entry,))
    context = context_for(root, catalogue=catalogue, **options)
    return CommandApplication(context, catalogue), context


def test_registry_closes_original_106_owners_and_single_new_command():
    ids = {entry.command_id for entry in current_application_catalogue().entries}
    validate_branch_effect_coverage(ids)
    assert len(ids - {"command.list", "command.describe", "invocation.read", "vault.configure-branch-guard"}) == 106
    assert BRANCH_EFFECTS["vault.configure-branch-guard"] is BranchEffect.CONFIGURATION
    for id in ("workspace.register", "workspace.rebind", "relationship.refresh", "retrieval.repair-semantic", "skill.status"):
        assert BRANCH_EFFECTS[id] is BranchEffect.ALLOWED
    for id in ("shaping.render", "maintenance.run"):
        assert BRANCH_EFFECTS[id] is BranchEffect.MIXED
    with pytest.raises(ValueError, match="missing"):
        validate_branch_effect_coverage(ids | {"new.writer"})


def test_wrong_branch_refuses_before_executor_and_intent(guarded_vault):
    root = guarded_vault
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    def execute(*_):
        pytest.fail("executor must not enter")
    app, context = application(root, ProtectedProbe, execute)
    before = read_local_config(root)
    result = app.invoke(ProtectedProbe())
    assert isinstance(result, Error) and result.effects == "none"
    assert result.error.details.reason == "branch-not-allowed"
    assert result.error.details.allowed_branches == ("dev", "other")
    assert context.authorisation.receipts.intents == {}
    assert read_local_config(root) == before


def test_permission_refusal_precedes_operation_capture(guarded_vault, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("permission denial must precede guard I/O")
    monkeypatch.setattr(branch_guard, "operation", forbidden)
    app, _ = application(guarded_vault, ProtectedProbe, forbidden, allowed_commands=())
    result = app.invoke(ProtectedProbe())
    assert result.error.code is ErrorCode.AUTHORITY_DENIED


def test_cache_mechanisms_remain_available_on_blocked_branch(guarded_vault):
    root = guarded_vault
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    def execute(context, request):
        (root / "cache-output").write_text("rebuildable")
        return Ok(request.COMMAND_ID, 1, ProbePayload())
    app, _ = application(root, CacheProbe, execute, protected=False)
    assert isinstance(app.invoke(CacheProbe()), Ok)
    assert (root / "cache-output").read_text() == "rebuildable"


def test_allowed_to_allowed_change_preserves_first_effect_without_rollback(guarded_vault):
    root = guarded_vault
    first, second = root / "first.md", root / "second.md"
    def execute(context, request):
        admit_owner(context, request, live_query)
        branch_guard.checkpoint(root)
        first.write_text("committed")
        git(root, "symbolic-ref", "HEAD", "refs/heads/other")
        try:
            branch_guard.checkpoint(root)
            second.write_text("must not happen")
        except branch_guard.BranchGuardError as exc:
            git(root, "symbolic-ref", "HEAD", "refs/heads/dev")
            with pytest.raises(branch_guard.BranchGuardError):
                branch_guard.checkpoint(root)
            from _application.application import branch_guard_error_result
            return Partial(request.COMMAND_ID, 1,
                branch_guard_error_result(request.COMMAND_ID, 1, exc).error,
                (CommittedEffect(request.COMMAND_ID, "first.md"),))
    app, _ = application(root, ProtectedProbe, execute)
    result = app.invoke(ProtectedProbe())
    assert isinstance(result, Partial)
    assert result.error.details.reason == "branch-changed"
    assert first.read_text() == "committed" and not second.exists()
    assert [effect.subject for effect in result.committed_effects] == ["first.md"]


def configure(root, value, revision, *, dry_run=False):
    catalogue = ApplicationCatalogue((configuration.catalogue_entry(),))
    context = context_for(root, catalogue=catalogue, dry_run=dry_run)
    return CommandApplication(context, catalogue).invoke(configuration.VaultConfigureBranchGuardRequest(value, revision))


def test_configuration_recovery_on_blocked_branch_preserves_settings(guarded_vault):
    root = guarded_vault
    git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    result = configure(root, ("dev",), read_local_config(root).revision)
    assert isinstance(result, Ok) and result.result.changed
    assert result.result.branch_guard.status == "blocked"
    assert read_local_config(root).document["defaults"] == {"custom": "retained"}
    assert len(result.committed_effects) == 1
    noop = configure(root, ("dev",), read_local_config(root).revision)
    assert isinstance(noop, Ok) and not noop.result.changed and not noop.committed_effects
    disabled = configure(root, None, read_local_config(root).revision)
    assert isinstance(disabled, Ok)
    assert "branch_guard" not in read_local_config(root).document


def test_configuration_cas_refusal_has_no_content_effects(guarded_vault):
    before = read_local_config(guarded_vault)
    result = configure(guarded_vault, None, "sha256:stale")
    assert isinstance(result, Error) and result.effects == "none"
    assert read_local_config(guarded_vault) == before


@pytest.mark.parametrize("payload", [{}, {"allowed_branches": []}, {"allowed_branches": "dev"}, {"allowed_branches": None}])
def test_configuration_requires_explicit_list_or_null_and_revision(payload):
    with pytest.raises(ValueError):
        configuration.decode(payload)


def test_typed_projection_and_request_discovery():
    request_type = configuration.VaultConfigureBranchGuardRequest
    schema = request_schema(request_type)
    assert set(schema["required"]) == {"allowed_branches", "expected_revision"}
    result_schema = result_payload_schema(request_type)
    assert result_schema["type"] == "object"
    assert "branch_guard" in result_schema["properties"]
    request = current_request_resolver().resolve(request_type.COMMAND_ID,
        {"allowed_branches": None, "expected_revision": "missing"})
    assert request == request_type(None, "missing")


def test_doctor_check_findings_have_no_automatic_repair(guarded_vault):
    assert branch_guard_findings(guarded_vault) == []
    git(guarded_vault, "symbolic-ref", "HEAD", "refs/heads/main")
    finding, = branch_guard_findings(guarded_vault)
    assert finding["check"] == "branch_guard" and finding["severity"] == "error"
    from _application.maintenance import _detection
    from _bootstrap.maintenance_findings import Identity
    classified, = _detection.classify([finding])
    assert classified.breach is None and classified.identity is Identity.EVIDENCE
    assert finding["code"] == "ineligible"
    assert finding["evidence"]["branch"] == "main"
    assert "repair" not in finding


def test_shared_and_template_policy_warn_without_merging():
    import config
    with pytest.warns(UserWarning, match="branch_guard.*ignored") as captured:
        merged = config._merge_config({"branch_guard": {"allowed_branches": ["main"]}},
            {"branch_guard": {"allowed_branches": ["other"]}}, {})
    assert len(captured) == 2 and "branch_guard" not in merged


def test_interactive_setup_is_explicit_and_skippable(tmp_path, monkeypatch):
    import configure as setup
    git(tmp_path, "init", "-q", "-b", "dev")
    messages = []
    assert setup.branch_guard_setup_request(tmp_path, ask=lambda _: "", emit=messages.append) is None
    assert read_local_config(tmp_path).revision == "missing"
    request = setup.branch_guard_setup_request(tmp_path, ask=lambda _: "yes", emit=messages.append)
    assert request == {"allowed_branches": ["dev"], "expected_revision": "missing"}
    assert any(str(tmp_path) in message for message in messages)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(branch_guard, "inspect_guard", lambda _: {
        "status": "unconfigured", "repository": None, "observed_branch": None})
    assert setup.branch_guard_setup_request(outside, ask=lambda _: pytest.fail("must not prompt")) is None


def test_interactive_dry_run_uses_existing_inspection_without_prompt(tmp_path, monkeypatch):
    import configure as setup
    from _command_interface import script
    monkeypatch.setattr(setup, "find_vault_root", lambda _: tmp_path)
    monkeypatch.setattr(setup, "branch_guard_setup_request",
                        lambda _: pytest.fail("read-only setup must not prompt"))
    calls = []
    monkeypatch.setattr(script, "run", lambda argv: calls.append(argv) or 0)
    assert setup.main(["branch-guard", "--interactive", "--dry-run", "--vault", str(tmp_path)]) == 0
    assert calls == [["vault", "read-config", "--vault", str(tmp_path)]]


def test_interactive_dry_run_does_not_ignore_conflicting_policy(tmp_path, monkeypatch):
    import configure as setup
    monkeypatch.setattr(setup, "find_vault_root", lambda _: tmp_path)
    monkeypatch.setattr(setup, "branch_guard_setup_request",
                        lambda _: pytest.fail("conflicting setup must not prompt"))
    assert setup.main(["branch-guard", "--interactive", "--dry-run",
                       "--allowed-branches", '["dev"]']) == 2


def test_unknown_outcome_retains_guard_diagnostics_and_owned_receipt(guarded_vault, monkeypatch):
    from _application.relationship import _native
    from _command_interface.receipts import _decode, _encode

    root = guarded_vault
    retained = []
    monkeypatch.setattr(_native, "retain_context_loss", lambda context: retained.append(context.invocation_id))
    def execute(context, request):
        admit_owner(context, request, live_query)
        branch_guard.checkpoint(root)
        (root / "first.md").write_text("committed")
        git(root, "symbolic-ref", "HEAD", "refs/heads/other")
        try:
            branch_guard.checkpoint(root)
        except branch_guard.BranchGuardError as exc:
            exc.record_completed("write", "first.md")
            exc.facts["retained_backup"] = ".brain/local/recovery/backup"
            raise
    app, context = application(root, ProtectedProbe, execute)
    result = app.invoke(ProtectedProbe())
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown" and retained == [context.invocation_id]
    details = result.error.details.branch_guard
    assert details.reason == "branch-changed"
    assert details.committed_paths == ("first.md",)
    assert details.recovery_locations[0].path == ".brain/local/recovery/backup"
    assert details.inspection_command == "vault.read-config"
    assert "branch-changed" in result.error.message
    outcome = context.authorisation.receipts.outcomes[context.invocation_id]
    assert outcome.receipt.branch_guard == details
    assert not outcome.receipt.committed_effects
    assert _decode(_encode(outcome.receipt)) == outcome.receipt
    assert canonical_result_envelope(result)["error"]["details"]["branch_guard"]["reason"] == "branch-changed"


@pytest.mark.parametrize("branches", [("dev/*",), ("dev..feature",), ("-unsafe",), ("dev", "dev")])
def test_request_uses_foundation_literal_validation(branches):
    with pytest.raises(ValueError, match="literal Git branch"):
        configuration.VaultConfigureBranchGuardRequest(branches, "missing")


def test_configuration_dry_run_checks_cas_without_writing(guarded_vault):
    before = read_local_config(guarded_vault)
    stale = configure(guarded_vault, ("dev",), "sha256:stale", dry_run=True)
    assert isinstance(stale, Error) and stale.effects == "none"
    preview = configure(guarded_vault, ("dev",), before.revision, dry_run=True)
    assert isinstance(preview, Ok) and not preview.result.changed
    assert not preview.committed_effects and read_local_config(guarded_vault) == before


def test_configuration_dry_run_requires_git_only_when_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(branch_guard, "observe_git", lambda _: branch_guard.GitState(None, None))
    before = read_local_config(tmp_path)
    setting = configure(tmp_path, ("dev",), before.revision, dry_run=True)
    assert isinstance(setting, Error) and setting.error.details.reason == "outside-git"
    removing = configure(tmp_path, None, before.revision, dry_run=True)
    assert isinstance(removing, Ok) and not removing.committed_effects
    assert read_local_config(tmp_path) == before


@pytest.mark.parametrize("locations", [[None], ["path"], [{}], [{"kind": "backup"}],
    [{"kind": "backup", "path": "retained", "extra": "refused"}]])
def test_guard_receipt_rejects_invalid_recovery_location_shapes(locations):
    from dataclasses import asdict
    from _application.types import BranchGuardDetails
    from _command_interface.receipts import _decode_branch_guard
    value = asdict(BranchGuardDetails("branch-changed", None, None, None, ()))
    value["recovery_locations"] = locations
    with pytest.raises(ValueError, match="recovery location"):
        _decode_branch_guard(value)


@pytest.mark.parametrize("reason", [None, "", " ", 42])
def test_guard_receipt_requires_nonempty_reason_string(reason):
    from dataclasses import asdict
    from _application.types import BranchGuardDetails
    from _command_interface.receipts import _decode_branch_guard
    value = asdict(BranchGuardDetails("branch-changed", None, None, None, ()))
    value["reason"] = reason
    with pytest.raises(ValueError, match="reason"):
        _decode_branch_guard(value)
