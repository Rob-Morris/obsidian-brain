"""Staged non-exiting grammar for composed local command discovery."""

from __future__ import annotations

import argparse
import json

from _launcher.discovery import LauncherCursor
from dataclasses import dataclass


_OWNERS = ("application", "launcher", "all")


class LocalCliUsageError(ValueError):
    """The local CLI grammar could not identify a valid request."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LocalCliUsageError(message)


@dataclass(frozen=True, slots=True)
class CommandListArguments:
    owner: str
    query: str | None
    domain: str | None
    availability: str | None
    authority: str | None
    dependency_tier: str | None
    locality: str | None
    effect_class: str | None
    retry_class: str | None
    projection: str | None
    refresh: bool
    page_size: int
    json_mode: bool
    view: str
    application_cursor: dict | None
    launcher_cursor: dict | None

    def application_payload(self) -> dict[str, object] | None:
        """Return only fields owned by selected-Brain `command.list`."""

        if self.owner == "launcher":
            return None
        payload = self._common_filters()
        payload["owner"] = "application"
        payload["refresh"] = self.refresh
        if self.view == "detailed":
            payload["view"] = self.view
        if self.application_cursor is not None:
            payload["cursor"] = self.application_cursor
        payload["page_size"] = self.page_size
        return payload

    def launcher_filters(self) -> dict[str, object] | None:
        """Return only fields owned by launcher manifest discovery."""

        if self.owner == "application":
            return None
        payload = self._common_filters()
        payload["page_size"] = self.page_size
        if self.launcher_cursor is not None:
            payload["cursor"] = LauncherCursor(**self.launcher_cursor)
        return payload

    def _common_filters(self) -> dict[str, object]:
        return {
            name: value
            for name, value in (
                ("query", self.query),
                ("domain", self.domain),
                ("availability", self.availability),
                ("authority", self.authority),
                ("dependency_tier", self.dependency_tier),
                ("locality", self.locality),
                ("effect_class", self.effect_class),
                ("retry_class", self.retry_class),
                ("projection", self.projection),
            )
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class CommandDescribeArguments:
    target_command_id: str
    owner: str
    json_mode: bool


DiscoveryArguments = CommandListArguments | CommandDescribeArguments


def build_discovery_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="brain")
    families = parser.add_subparsers(dest="family", required=True)
    command = families.add_parser("command")
    actions = command.add_subparsers(dest="action", required=True)

    listing = actions.add_parser("list")
    _add_owner(listing)
    listing.add_argument("--query")
    listing.add_argument("--domain")
    listing.add_argument("--availability")
    listing.add_argument("--authority")
    listing.add_argument("--dependency-tier", dest="dependency_tier")
    listing.add_argument("--locality")
    listing.add_argument("--effect-class", dest="effect_class")
    listing.add_argument("--retry-class", dest="retry_class")
    listing.add_argument("--projection")
    listing.add_argument("--refresh", action="store_true")
    listing.add_argument("--page-size", type=int, default=25)
    listing.add_argument("--view", choices=("brief", "detailed"), default="brief")
    listing.add_argument("--application-cursor", type=_cursor_json)
    listing.add_argument("--launcher-cursor", type=_cursor_json)
    listing.add_argument("--json", dest="json_mode", action="store_true")

    describe = actions.add_parser("describe")
    describe.add_argument("target_command_id")
    _add_owner(describe)
    describe.add_argument("--json", dest="json_mode", action="store_true")
    return parser


def parse_discovery_arguments(argv: list[str]) -> DiscoveryArguments:
    """Parse only staged list/describe grammar without exiting the process."""

    values = vars(build_discovery_parser().parse_args(argv))
    action = values.pop("action")
    values.pop("family")
    if action == "list":
        if not 1 <= values["page_size"] <= 500:
            raise LocalCliUsageError("--page-size must be between 1 and 500")
        if values["owner"] == "launcher" and values["refresh"]:
            raise LocalCliUsageError("--refresh is available only for application discovery")
        if values["owner"] == "launcher" and values["application_cursor"] is not None:
            raise LocalCliUsageError("--application-cursor requires application discovery")
        if values["owner"] == "application" and values["launcher_cursor"] is not None:
            raise LocalCliUsageError("--launcher-cursor requires launcher discovery")
        if values["launcher_cursor"] is not None:
            try:
                LauncherCursor(**values["launcher_cursor"])
            except (TypeError, ValueError) as exc:
                raise LocalCliUsageError("invalid launcher cursor: " + str(exc)) from exc
        return CommandListArguments(**values)
    return CommandDescribeArguments(**values)


def parser_exit_code() -> int:
    return 2


def _add_owner(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner", choices=_OWNERS, default="all")


def _cursor_json(value):
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("cursor must be a JSON object") from exc
    if not isinstance(result, dict):
        raise argparse.ArgumentTypeError("cursor must be a JSON object")
    return result


def discovery_request_argv(argv: list[str], request: dict[str, object]) -> list[str]:
    """Route structured discovery input through the same grammar as flags."""
    if len(argv) != 2:
        raise LocalCliUsageError("Use either discovery flags or --request-json, not both")
    allowed = ({"owner", "query", "domain", "availability", "authority",
                "dependency_tier", "locality", "effect_class", "retry_class",
                "projection", "refresh", "page_size", "view", "cursor",
                "application_cursor", "launcher_cursor"}
               if argv[1] == "list" else {"owner", "target_command_id"})
    if set(request) - allowed:
        raise LocalCliUsageError("Unknown discovery request fields: " + ", ".join(sorted(set(request) - allowed)))
    if "cursor" in request and "application_cursor" in request:
        raise LocalCliUsageError("Use only one application cursor spelling")
    result = list(argv)
    for key, value in request.items():
        if value is None:
            continue
        if key == "refresh":
            if not isinstance(value, bool):
                raise LocalCliUsageError("refresh must be a boolean")
            if value:
                result.append("--refresh")
            continue
        if key == "page_size":
            if not isinstance(value, int) or isinstance(value, bool):
                raise LocalCliUsageError("page_size must be an integer")
        elif key.endswith("cursor"):
            if not isinstance(value, dict):
                raise LocalCliUsageError("cursor must be a JSON object")
        elif not isinstance(value, str):
            raise LocalCliUsageError(key + " must be a string")
        if key == "target_command_id":
            result.append(value)
        else:
            flag = "application_cursor" if key == "cursor" else key
            result.extend(["--" + flag.replace("_", "-"),
                json.dumps(value) if flag.endswith("cursor") else str(value)])
    return result
