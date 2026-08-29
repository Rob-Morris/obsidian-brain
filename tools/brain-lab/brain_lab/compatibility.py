from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


def parse_version(value: str) -> tuple[int, int, int]:
    raw = value.strip().removeprefix("v")
    parts = raw.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"unsupported Brain version format: {value!r}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


@dataclass(frozen=True)
class GateRetry:
    maximum_attempts: int
    retryable_error_codes: tuple[str, ...]
    maximum_delay_seconds: float


@dataclass(frozen=True)
class HealthGate:
    gate_id: str
    command: tuple[str, ...]
    expected_stdout: str | None = None
    expected_json: Mapping[str, Any] | None = None
    required_for: tuple[str, ...] = ()
    preserve_safe: bool = False
    timeout_seconds: float = 300
    retry: GateRetry | None = None


@dataclass(frozen=True)
class CompatibilityAdapter:
    adapter_id: str
    revision: int
    minimum_version: str
    maximum_version_exclusive: str
    install: tuple[str, ...]
    post_install: tuple[tuple[str, ...], ...]
    template_prepare: tuple[tuple[str, ...], ...]
    rehydrate: tuple[tuple[str, ...], ...]
    health: tuple[HealthGate, ...]

    def supports(self, version: str) -> bool:
        parsed = parse_version(version)
        return parse_version(self.minimum_version) <= parsed < parse_version(self.maximum_version_exclusive)

    def render(self, command: Sequence[str], values: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(item.format_map(values) for item in command)


class CompatibilityManifest:
    def __init__(self, path: Path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "brain-lab.compatibility/1":
            raise ValueError(f"unsupported compatibility manifest schema: {path}")
        adapters = []
        for item in payload.get("adapters", []):
            health = []
            for gate in item["health"]:
                timeout = gate.get("timeout_seconds", 300)
                if not isinstance(timeout, (int, float)) or not 0 < timeout <= 1800:
                    raise ValueError(f"health gate {gate['id']} has invalid timeout_seconds")
                retry_payload = gate.get("retry")
                retry = None
                if retry_payload is not None:
                    attempts = retry_payload.get("maximum_attempts")
                    codes = retry_payload.get("retryable_error_codes")
                    delay = retry_payload.get("maximum_delay_seconds")
                    if not isinstance(attempts, int) or not 2 <= attempts <= 100:
                        raise ValueError(f"health gate {gate['id']} has invalid maximum_attempts")
                    if not isinstance(codes, list) or not codes or not all(
                        isinstance(code, str) and code for code in codes
                    ):
                        raise ValueError(f"health gate {gate['id']} has invalid retryable_error_codes")
                    if not isinstance(delay, (int, float)) or not 0 <= delay <= 10:
                        raise ValueError(f"health gate {gate['id']} has invalid maximum_delay_seconds")
                    retry = GateRetry(attempts, tuple(codes), float(delay))
                health.append(
                    HealthGate(
                        gate_id=gate["id"],
                        command=tuple(gate["command"]),
                        expected_stdout=gate.get("expected_stdout"),
                        expected_json=gate.get("expected_json"),
                        required_for=tuple(gate.get("required_for", [])),
                        preserve_safe=gate.get("preserve_safe", False),
                        timeout_seconds=float(timeout),
                        retry=retry,
                    )
                )
            adapters.append(
                CompatibilityAdapter(
                    adapter_id=item["id"],
                    revision=item["revision"],
                    minimum_version=item["minimum_version"],
                    maximum_version_exclusive=item["maximum_version_exclusive"],
                    install=tuple(item["install"]),
                    post_install=tuple(tuple(command) for command in item.get("post_install", [])),
                    template_prepare=tuple(
                        tuple(command) for command in item.get("template_prepare", [])
                    ),
                    rehydrate=tuple(tuple(command) for command in item["rehydrate"]),
                    health=tuple(health),
                )
            )
        if not adapters:
            raise ValueError("compatibility manifest contains no adapters")
        self.adapters = tuple(adapters)

    def select(self, version: str) -> CompatibilityAdapter:
        matches = [adapter for adapter in self.adapters if adapter.supports(version)]
        if len(matches) != 1:
            raise ValueError(f"Brain {version} has no unique supported compatibility adapter")
        return matches[0]
