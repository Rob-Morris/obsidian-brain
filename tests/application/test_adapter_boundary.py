"""Shared dynamic adapter result and failure-category contracts."""

from __future__ import annotations

from _application.adapter import (
    AdapterRequestError,
    ApplicationAdapter,
    parser_exit_code,
    project_adapter_result,
)
from _application.receipts import CommittedEffect, OutcomeReference
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import (
    CapabilityUnavailableDetails,
    CommandError,
    Error,
    ErrorCode,
    Ok,
    OutcomeUnknownDetails,
    Partial,
)
from _application.types import DependencyTier, Locality, SnapshotFreshness

from command_application import application_for


def _adapter():
    return ApplicationAdapter(
        current_application_catalogue(),
        current_request_resolver(),
    )


def test_dynamic_adapter_resolves_and_invokes_one_owned_request(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)
    context = application._context

    projected = _adapter().invoke(context, "command.list", {"page_size": 1})

    assert projected.result.status == "ok"
    assert projected.structured_content["command"] == "command.list"
    assert len(projected.structured_content["result"]["entries"]) == 1
    assert projected.json_text.startswith('{"schema":"brain.command-result/1"')
    assert projected.concise_text == "command.list: ok"
    assert projected.is_error is False
    assert projected.exit_code == 0


def test_dynamic_adapter_structures_known_invalid_and_rejects_unknown_identity(
    command_vault_clone,
):
    context = application_for(command_vault_clone.vault_root)._context
    invalid = _adapter().invoke(
        context,
        "vault.read-file",
        {"path": "README.md", "command_version": 1},
    )

    assert invalid.result.error.code is ErrorCode.INVALID_REQUEST
    assert invalid.structured_content["error"]["details"]["reason"].startswith(
        "command identity"
    )
    assert invalid.exit_code == 2
    for command_id in ("unknown.command", "not canonical"):
        try:
            _adapter().invoke(context, command_id, {})
        except AdapterRequestError as exc:
            assert exc.code.value in {"unknown_command", "invalid_request"}
        else:
            raise AssertionError("unknown dynamic adapter identity unexpectedly resolved")
    assert parser_exit_code() == 2


def test_result_projection_preserves_branches_and_exact_exit_categories():
    unavailable = Error(
        "vault.read-file",
        1,
        CommandError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "Unavailable.",
            CapabilityUnavailableDetails(
                DependencyTier.MANAGED,
                DependencyTier.PORTABLE,
                Locality.SELECTED_BRAIN_LOCAL,
                ("provider:example",),
                SnapshotFreshness.FRESH,
                True,
            ),
        ),
    )
    reference = OutcomeReference("inv-unknown")
    cases = (
        (Ok("vault.read-file", 1, "value"), False, 0),
        (
            Partial(
                "artefact.delete",
                1,
                CommandError(ErrorCode.CONFLICT, "Partial."),
                (CommittedEffect("artefact.delete", "Example.md"),),
            ),
            True,
            1,
        ),
        (
            Error(
                "vault.read-file",
                1,
                CommandError(ErrorCode.INVALID_REQUEST, "Invalid."),
            ),
            True,
            2,
        ),
        (unavailable, True, 3),
        (
            Error(
                "artefact.delete",
                1,
                CommandError(
                    ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                    "Unknown.",
                    OutcomeUnknownDetails(reference),
                ),
                effects="unknown",
                outcome_reference=reference,
            ),
            True,
            4,
        ),
    )

    for result, is_error, exit_code in cases:
        projected = project_adapter_result(result)
        assert projected.is_error is is_error
        assert projected.exit_code == exit_code
        assert projected.structured_content["status"] == result.status
