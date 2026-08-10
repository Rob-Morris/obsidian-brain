"""Staged non-exiting grammar for composed local command discovery."""

from __future__ import annotations

import argparse
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

    def application_payload(self) -> dict[str, object] | None:
        """Return only fields owned by selected-Brain `command.list`."""

        if self.owner == "launcher":
            return None
        payload = self._common_filters()
        payload["owner"] = "application"
        payload["refresh"] = self.refresh
        payload["page_size"] = self.page_size
        return payload

    def launcher_filters(self) -> dict[str, object] | None:
        """Return only fields owned by launcher manifest discovery."""

        if self.owner == "application":
            return None
        payload = self._common_filters()
        payload["page_size"] = self.page_size
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
    listing.add_argument("--page-size", type=int, default=100)
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
        return CommandListArguments(**values)
    return CommandDescribeArguments(**values)


def parser_exit_code() -> int:
    return 2


def _add_owner(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner", choices=_OWNERS, default="all")
