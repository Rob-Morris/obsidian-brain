"""Pure normal-work approval policy over canonical release contract facts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re


CONTRACT_SCHEMA = "brain.approval-contract/1"
POLICY = "brain-normal/1"
_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_CONTROLS = {
    "access.status": "allow", "access.prepare": "allow", "access.request": "prompt",
    "access.reduce": "allow", "command.list": "allow", "command.describe": "allow",
    "invocation.read": "allow", "session.start": "allow",
}


@dataclass(frozen=True, slots=True)
class CommandFact:
    command_id: str
    version: int
    owner: str
    classification: str
    mcp: str | None
    cli: tuple[str, ...]
    authority: str = "reader"

    def __post_init__(self):
        if self.owner not in {"application", "launcher", "proxy"}:
            raise ValueError("unknown approval contract owner")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("approval contract requires a positive version")
        if not isinstance(self.command_id, str) or not self.command_id:
            raise ValueError("approval contract requires command identity")
        if self.mcp is not None and not _NAME.fullmatch(self.mcp):
            raise ValueError("invalid native MCP identity in approval contract")
        if not isinstance(self.cli, tuple) or any(not _NAME.fullmatch(part) for part in self.cli):
            raise ValueError("approval CLI identity must contain literal command words")
        if not isinstance(self.classification, str):
            raise ValueError("approval classification must be text")
        if not isinstance(self.authority, str) or not self.authority:
            raise ValueError("approval authority must be non-empty text")


def decision(fact: CommandFact) -> str:
    """Unknown contracts acquire no allowance, independently of user credentials."""
    if fact.owner == "application":
        if fact.classification in {"observation", "content"}:
            return "allow"
        if fact.classification == "exceptional":
            return "prompt"
        if fact.classification == "control":
            return _CONTROLS.get(fact.command_id, "unknown")
    elif fact.owner == "launcher":
        if fact.classification == "none" and fact.authority != "reader":
            return "prompt"
        return {"none": "allow", "machine_mutation": "prompt"}.get(fact.classification, "unknown")
    elif fact.owner == "proxy":
        return {"status": "allow", "refresh": "prompt", "restart": "prompt"}.get(fact.classification, "unknown")
    return "unknown"


def snapshot(facts: tuple[CommandFact, ...]) -> dict:
    """Encode generated facts, not a separately authored command catalogue."""
    entries = [{"command_id": f.command_id, "version": f.version, "owner": f.owner,
                "classification": f.classification, "mcp": f.mcp, "cli": list(f.cli), "authority": f.authority}
               for f in sorted(facts, key=lambda f: (f.owner, f.command_id))]
    return {"schema": CONTRACT_SCHEMA, "entries": entries,
            "fingerprint": "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()}


def read_snapshot(value: object) -> tuple[CommandFact, ...]:
    """Validate a release-file boundary, including identities and bounded size."""
    if not isinstance(value, dict) or value.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("unsupported approval contract; upgrade or repair its installation")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > 4096:
        raise ValueError("invalid approval contract entries")
    facts = []
    try:
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("cli"), list):
                raise ValueError("invalid approval command fact")
            facts.append(CommandFact(**{**item, "cli": tuple(item["cli"])}))
    except TypeError as exc:
        raise ValueError("invalid approval command fact fields") from exc
    identities = [(f.owner, f.command_id) for f in facts]
    if len(identities) != len(set(identities)) or snapshot(tuple(facts)) != value:
        raise ValueError("approval contract fingerprint or identities do not match")
    return tuple(facts)


def desired_policy(contracts: tuple[tuple[CommandFact, ...], ...], surface: str) -> dict[str, str]:
    """Meet classifications for each native identity across known target versions."""
    if surface not in {"mcp", "cli"}:
        raise ValueError("approval surface must be mcp or cli")
    grouped: dict[str, set[str]] = {}
    for facts in contracts:
        local = set()
        for fact in facts:
            identity = fact.mcp if surface == "mcp" else json.dumps(fact.cli) if fact.cli else None
            if identity is None:
                continue
            if identity in local:
                raise ValueError(f"duplicate native approval identity: {identity}")
            local.add(identity)
            grouped.setdefault(identity, set()).add(decision(fact))
    return {identity: "allow" if choices == {"allow"} else "prompt" if "unknown" not in choices else "unknown"
            for identity, choices in sorted(grouped.items())}
