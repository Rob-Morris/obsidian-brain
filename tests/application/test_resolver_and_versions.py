"""Dynamic request resolution and independent version-transition contracts."""

from __future__ import annotations

import pytest

from _application.requests import (
    CommandDescribeRequest,
    CommandListRequest,
)
from _application.resolver import (
    RequestResolutionError,
    RequestResolver,
    ResolutionErrorCode,
    ResolverEntry,
)
from _application.versions import (
    ChangeKind,
    CommandVersion,
    ContractChange,
    InterfaceVersions,
    replay_identity_compatible,
    validate_transition,
)


def _resolver():
    return RequestResolver(
        (
            ResolverEntry(
                CommandDescribeRequest,
                lambda payload: CommandDescribeRequest(payload["target_command_id"]),
            ),
            ResolverEntry(
                CommandListRequest,
                lambda payload: CommandListRequest(**payload),
            ),
        )
    )


def test_dynamic_resolver_produces_only_the_registered_typed_pairing():
    resolver = _resolver()

    request = resolver.resolve("command.describe", {"target_command_id": "artefact.read"})

    assert type(request) is CommandDescribeRequest
    assert request.target_command_id == "artefact.read"


@pytest.mark.parametrize("reserved", ["command_id", "command_version"])
def test_dynamic_resolver_rejects_caller_supplied_identity(reserved):
    with pytest.raises(RequestResolutionError) as exc:
        _resolver().resolve("command.list", {reserved: "forged"})

    assert exc.value.code is ResolutionErrorCode.INVALID_REQUEST


def test_local_resolution_binds_installed_version_and_serialised_mismatch_fails():
    resolver = _resolver()
    assert type(resolver.resolve("command.list", {})) is CommandListRequest

    with pytest.raises(RequestResolutionError) as exc:
        resolver.resolve("command.list", {}, expected_version=1)

    assert exc.value.code is ResolutionErrorCode.UNSUPPORTED_COMMAND_VERSION


def test_unknown_invalid_and_wrong_decoder_requests_fail_before_execution():
    resolver = _resolver()
    with pytest.raises(RequestResolutionError) as unknown:
        resolver.resolve("artefact.read", {})
    assert unknown.value.code is ResolutionErrorCode.UNKNOWN_COMMAND

    with pytest.raises(RequestResolutionError) as invalid:
        resolver.resolve("command.list", {"page_size": 0})
    assert invalid.value.code is ResolutionErrorCode.INVALID_REQUEST

    wrong = RequestResolver(
        (ResolverEntry(CommandListRequest, lambda _payload: CommandDescribeRequest("artefact.read")),)
    )
    with pytest.raises(RequestResolutionError, match="wrong request type"):
        wrong.resolve("command.list", {})


def _versions(**changes):
    values = {
        "interface_epoch": 1,
        "result_schema_version": 1,
        "catalogue_schema_version": 1,
        "launcher_schema_version": 1,
        "proxy_protocol_version": 1,
        "commands": (CommandVersion("artefact.read", 1),),
    }
    values.update(changes)
    return InterfaceVersions(**values)


@pytest.mark.parametrize(
    ("change", "current"),
    [
        (ContractChange(ChangeKind.REQUEST_PROJECTION_BREAKING), _versions(interface_epoch=2)),
        (
            ContractChange(ChangeKind.COMMAND_CONTRACT_BREAKING, "artefact.read"),
            _versions(commands=(CommandVersion("artefact.read", 2),)),
        ),
        (ContractChange(ChangeKind.RESULT_SCHEMA_BREAKING), _versions(result_schema_version=2)),
        (ContractChange(ChangeKind.CATALOGUE_SCHEMA_BREAKING), _versions(catalogue_schema_version=2)),
        (ContractChange(ChangeKind.LAUNCHER_SCHEMA_BREAKING), _versions(launcher_schema_version=2)),
        (ContractChange(ChangeKind.PROXY_PROTOCOL_BREAKING), _versions(proxy_protocol_version=2)),
        (ContractChange(ChangeKind.COMPATIBLE_ADDITION), _versions()),
        (ContractChange(ChangeKind.FINGERPRINT_ONLY), _versions()),
    ],
)
def test_each_change_kind_is_owned_by_its_independent_version(change, current):
    validate_transition(_versions(), current, (change,))


@pytest.mark.parametrize(
    "change",
    [
        ContractChange(ChangeKind.REQUEST_PROJECTION_BREAKING),
        ContractChange(ChangeKind.COMMAND_CONTRACT_BREAKING, "artefact.read"),
        ContractChange(ChangeKind.RESULT_SCHEMA_BREAKING),
        ContractChange(ChangeKind.CATALOGUE_SCHEMA_BREAKING),
        ContractChange(ChangeKind.LAUNCHER_SCHEMA_BREAKING),
        ContractChange(ChangeKind.PROXY_PROTOCOL_BREAKING),
    ],
)
def test_breaking_change_without_owning_version_increment_fails(change):
    with pytest.raises(ValueError, match="requires"):
        validate_transition(_versions(), _versions(), (change,))


def test_replay_uses_epoch_command_version_and_mutation_not_fingerprint():
    facts = {
        "accepted_epoch": 2,
        "accepted_command_id": "artefact.create",
        "accepted_command_version": 3,
        "accepted_mutation_class": "selected_brain_mutation",
        "replacement_epoch": 2,
        "replacement_command_id": "artefact.create",
        "replacement_command_version": 3,
        "replacement_mutation_class": "selected_brain_mutation",
    }
    assert replay_identity_compatible(**facts)
    for field, incompatible in (
        ("replacement_epoch", 3),
        ("replacement_command_id", "document.edit"),
        ("replacement_command_version", 4),
        ("replacement_mutation_class", "none"),
    ):
        changed = dict(facts)
        changed[field] = incompatible
        assert not replay_identity_compatible(**changed)
