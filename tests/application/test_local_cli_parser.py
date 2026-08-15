"""Staged composed command-discovery CLI grammar."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


CLI_DIR = Path(__file__).resolve().parents[2] / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))


from _application.registry import current_request_resolver
from _local_cli.parser import (
    CommandDescribeArguments,
    CommandListArguments,
    LocalCliUsageError,
    parse_discovery_arguments,
    parser_exit_code,
)
from _launcher.discovery import list_commands


def test_command_list_parses_explicit_owner_and_shared_filters():
    arguments = parse_discovery_arguments(
        [
            "command",
            "list",
            "--owner",
            "all",
            "--query",
            "read",
            "--domain",
            "vault",
            "--availability",
            "available",
            "--authority",
            "reader",
            "--dependency-tier",
            "portable",
            "--locality",
            "selected_brain_local",
            "--effect-class",
            "none",
            "--retry-class",
            "safe",
            "--projection",
            "cli",
            "--refresh",
            "--page-size",
            "25",
            "--json",
        ]
    )

    assert isinstance(arguments, CommandListArguments)
    assert arguments.owner == "all"
    assert arguments.refresh is True
    assert arguments.page_size == 25
    assert arguments.json_mode is True


def test_command_list_projects_only_each_authoritative_owner_fields():
    resolver = current_request_resolver()
    application = parse_discovery_arguments(
        ["command", "list", "--owner", "application", "--domain", "vault"]
    )
    launcher = parse_discovery_arguments(
        ["command", "list", "--owner", "launcher", "--domain", "brain"]
    )

    application_payload = application.application_payload()
    assert application.launcher_filters() is None
    assert type(resolver.resolve("command.list", application_payload)).__name__ == (
        "CommandListRequest"
    )
    assert launcher.application_payload() is None
    assert tuple(
        entry.command_id for entry in list_commands(**launcher.launcher_filters()).entries
    ) == (
        "brain.clear-default",
        "brain.doctor",
        "brain.get-default",
        "brain.install",
        "brain.list",
        "brain.migrate-legacy-installations",
        "brain.register",
        "brain.resolve",
        "brain.set-default",
        "brain.uninstall",
        "brain.unregister",
        "brain.upgrade",
        "brain.version",
    )


def test_command_list_all_keeps_refresh_application_owned():
    arguments = parse_discovery_arguments(
        ["command", "list", "--owner", "all", "--refresh"]
    )

    assert arguments.application_payload()["refresh"] is True
    assert "refresh" not in arguments.launcher_filters()


def test_command_describe_preserves_target_and_owner_provenance():
    arguments = parse_discovery_arguments(
        [
            "command",
            "describe",
            "artefact.create",
            "--owner",
            "application",
            "--json",
        ]
    )

    assert arguments == CommandDescribeArguments(
        "artefact.create",
        "application",
        True,
    )


@pytest.mark.parametrize(
    "argv, message",
    (
        (["command", "list", "--owner", "unknown"], "invalid choice"),
        (["command", "list", "--page-size", "0"], "between 1 and 500"),
        (
            ["command", "list", "--owner", "launcher", "--refresh"],
            "only for application discovery",
        ),
        (["command", "describe"], "required"),
        (["artefact", "read"], "invalid choice"),
    ),
)
def test_discovery_parser_fails_without_exiting(argv, message):
    with pytest.raises(LocalCliUsageError, match=message):
        parse_discovery_arguments(argv)
    assert parser_exit_code() == 2
