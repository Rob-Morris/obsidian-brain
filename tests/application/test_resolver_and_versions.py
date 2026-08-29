"""Dynamic request resolution contracts."""

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
