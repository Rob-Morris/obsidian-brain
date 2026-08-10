"""Strict stdlib-only contracts for the MCP proxy/server command interface."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping


PROXY_PROTOCOL = 2
MIN_PROXY_PROTOCOL = 2
MAX_PROXY_PROTOCOL = 2
INTERFACE_HEADER_SCHEMA = "brain.command-interface-header/1"
INTERFACE_HEADER_EXTENSION = "brainCommandInterface"
_ASCII_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
_ASCII_DIGITS = frozenset("0123456789")
_ASCII_ALNUM = _ASCII_LOWER | _ASCII_DIGITS
_TOOL_NAME_CHARACTERS = _ASCII_ALNUM | {"_"}
_COMMAND_PART_CHARACTERS = _ASCII_ALNUM | {"-"}


@dataclass(frozen=True, slots=True)
class InterfaceTool:
    command_id: str
    command_version: int
    mutation_class: str

    def __post_init__(self) -> None:
        _command_id(self.command_id)
        _positive_int(self.command_version, "command version")
        _non_empty(self.mutation_class, "mutation class")


@dataclass(frozen=True, slots=True)
class CommandInterfaceHeader:
    interface_epoch: int
    catalogue_schema: str
    result_schema: str
    catalogue_fingerprint: str
    tools: tuple[tuple[str, InterfaceTool], ...]
    minimum_proxy_protocol: int = MIN_PROXY_PROTOCOL
    maximum_proxy_protocol: int = MAX_PROXY_PROTOCOL
    schema: str = INTERFACE_HEADER_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != INTERFACE_HEADER_SCHEMA:
            raise ValueError(f"unsupported command-interface header schema: {self.schema}")
        minimum = _positive_int(self.minimum_proxy_protocol, "minimum proxy protocol")
        maximum = _positive_int(self.maximum_proxy_protocol, "maximum proxy protocol")
        if minimum > maximum:
            raise ValueError("proxy protocol range is inverted")
        _positive_int(self.interface_epoch, "command interface epoch")
        _schema_name(self.catalogue_schema, "brain.command-catalogue/")
        _schema_name(self.result_schema, "brain.command-result/")
        _fingerprint(self.catalogue_fingerprint, "catalogue fingerprint")
        names = [name for name, _ in self.tools]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("interface tool mappings must be sorted and unique")
        for name, mapping in self.tools:
            _tool_name(name)
            if not isinstance(mapping, InterfaceTool):
                raise TypeError("interface tool mappings must use InterfaceTool")
            expected_name = "brain_" + mapping.command_id.replace(".", "_").replace("-", "_")
            if name != expected_name:
                raise ValueError(
                    "projected MCP tool name contradicts its command identifier"
                )

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            _header_payload(self),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def tool(self, name: str) -> InterfaceTool | None:
        return dict(self.tools).get(name)


def command_interface_header(
    *,
    interface_epoch: int,
    catalogue_schema: str,
    result_schema: str,
    catalogue_fingerprint: str,
    tools: Mapping[str, InterfaceTool],
) -> CommandInterfaceHeader:
    """Build one deterministic header from an authoritative catalogue projection."""

    return CommandInterfaceHeader(
        interface_epoch=interface_epoch,
        catalogue_schema=catalogue_schema,
        result_schema=result_schema,
        catalogue_fingerprint=catalogue_fingerprint,
        tools=tuple(sorted(tools.items())),
    )


def command_interface_wire(header: CommandInterfaceHeader) -> dict[str, object]:
    """Serialise a validated header with a self-verifying static fingerprint."""

    payload = _header_payload(header)
    payload["fingerprint"] = header.fingerprint
    return payload


def parse_command_interface_header(raw: object) -> CommandInterfaceHeader:
    """Parse an initialise extension without accepting missing or extra facts."""

    if not isinstance(raw, Mapping):
        raise ValueError("command-interface header must be an object")
    _exact_fields(
        raw,
        {
            "schema",
            "proxy_protocol",
            "interface_epoch",
            "catalogue_schema",
            "result_schema",
            "catalogue_fingerprint",
            "tools",
            "fingerprint",
        },
        "command-interface header",
    )
    protocol = raw["proxy_protocol"]
    if not isinstance(protocol, Mapping):
        raise ValueError("proxy_protocol must be an object")
    _exact_fields(protocol, {"minimum", "maximum"}, "proxy_protocol")
    raw_tools = raw["tools"]
    if not isinstance(raw_tools, Mapping):
        raise ValueError("interface tools must be an object")
    tools = []
    for name, value in raw_tools.items():
        _tool_name(name)
        if not isinstance(value, Mapping):
            raise ValueError(f"interface tool mapping must be an object: {name}")
        _exact_fields(
            value,
            {"command_id", "command_version", "mutation_class"},
            f"interface tool mapping {name}",
        )
        tools.append(
            (
                name,
                InterfaceTool(
                    command_id=value["command_id"],
                    command_version=value["command_version"],
                    mutation_class=value["mutation_class"],
                ),
            )
        )
    header = CommandInterfaceHeader(
        schema=raw["schema"],
        minimum_proxy_protocol=protocol["minimum"],
        maximum_proxy_protocol=protocol["maximum"],
        interface_epoch=raw["interface_epoch"],
        catalogue_schema=raw["catalogue_schema"],
        result_schema=raw["result_schema"],
        catalogue_fingerprint=raw["catalogue_fingerprint"],
        tools=tuple(tools),
    )
    fingerprint = raw["fingerprint"]
    _fingerprint(fingerprint, "header fingerprint")
    if fingerprint != header.fingerprint:
        raise ValueError("command-interface header fingerprint does not match its content")
    return header


def interface_header_from_initialize(response: object) -> CommandInterfaceHeader:
    """Extract the extension from an MCP initialise result and validate it."""

    if not isinstance(response, Mapping):
        raise ValueError("initialize response must be an object")
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("initialize response requires a result")
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, Mapping):
        raise ValueError("initialize result requires capabilities")
    experimental = capabilities.get("experimental")
    if not isinstance(experimental, Mapping):
        raise ValueError("initialize result requires experimental capabilities")
    if INTERFACE_HEADER_EXTENSION not in experimental:
        raise ValueError("initialize result is missing brainCommandInterface")
    return parse_command_interface_header(experimental[INTERFACE_HEADER_EXTENSION])


def proxy_protocol_supported(header: CommandInterfaceHeader, protocol: int) -> bool:
    """Return whether the running proxy protocol is inside the declared range."""

    _positive_int(protocol, "running proxy protocol")
    return header.minimum_proxy_protocol <= protocol <= header.maximum_proxy_protocol


def _header_payload(header: CommandInterfaceHeader) -> dict[str, object]:
    return {
        "schema": header.schema,
        "proxy_protocol": {
            "minimum": header.minimum_proxy_protocol,
            "maximum": header.maximum_proxy_protocol,
        },
        "interface_epoch": header.interface_epoch,
        "catalogue_schema": header.catalogue_schema,
        "result_schema": header.result_schema,
        "catalogue_fingerprint": header.catalogue_fingerprint,
        "tools": {
            name: {
                "command_id": mapping.command_id,
                "command_version": mapping.command_version,
                "mutation_class": mapping.mutation_class,
            }
            for name, mapping in header.tools
        },
    }


def _exact_fields(value: Mapping, expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} fields do not match the contract: "
            f"missing={sorted(expected - actual)} unknown={sorted(actual - expected)}"
        )


def _non_empty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _schema_name(value: object, prefix: str) -> None:
    text = _non_empty(value, "schema")
    if not text.startswith(prefix) or not text.removeprefix(prefix).isdigit():
        raise ValueError(f"schema must use {prefix}<version>")


def _fingerprint(value: object, label: str) -> None:
    text = _non_empty(value, label)
    if not text.startswith("sha256:") or len(text) != 71:
        raise ValueError(f"{label} must be a sha256 fingerprint")
    try:
        int(text.removeprefix("sha256:"), 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a sha256 fingerprint") from exc


def _command_id(value: object) -> None:
    text = _non_empty(value, "command identifier")
    parts = text.split(".")
    if len(parts) != 2 or any(not _canonical_part(part) for part in parts):
        raise ValueError("command identifier must use canonical noun.verb grammar")


def _tool_name(value: object) -> None:
    text = _non_empty(value, "projected MCP tool name")
    if not text.startswith("brain_") or any(
        character not in _TOOL_NAME_CHARACTERS
        for character in text
    ):
        raise ValueError("projected MCP tool name must use brain_<noun>_<verb> grammar")


def _canonical_part(value: str) -> bool:
    return (
        bool(value)
        and value[0] in _ASCII_LOWER
        and value[-1] in _ASCII_ALNUM
        and all(character in _COMMAND_PART_CHARACTERS for character in value)
    )
