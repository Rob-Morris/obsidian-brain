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


def render_expected(value: Any, values: Mapping[str, str]) -> Any:
    """Format every string in an expected value with the gate's placeholders."""
    if isinstance(value, str):
        return value.format_map(values)
    if isinstance(value, list):
        return [render_expected(item, values) for item in value]
    if isinstance(value, dict):
        return {key: render_expected(item, values) for key, item in value.items()}
    return value


def json_value(payload: Any, path: str) -> Any:
    """Look a dotted path up in a JSON payload; KeyError when any step is missing."""
    value = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def gate_output_matches(gate: HealthGate, stdout: str, values: Mapping[str, str]) -> bool:
    """Whether a gate command's stdout meets its expected stdout and expected JSON paths."""
    expected = gate.expected_stdout.format_map(values) if gate.expected_stdout else None
    matched = expected is None or stdout.strip() == expected
    if gate.expected_json:
        expected_json = render_expected(gate.expected_json, values)
        try:
            payload = json.loads(stdout)
            matched = matched and all(
                json_value(payload, path) == value for path, value in expected_json.items()
            )
        except (json.JSONDecodeError, KeyError):
            matched = False
    return matched


def gate_retry_delay(gate: HealthGate, stdout: str) -> float | None:
    """Seconds to wait before retrying a failed gate attempt, or None when its error is not retryable."""
    if gate.retry is None:
        return None
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    error = envelope.get("error") if isinstance(envelope, dict) else None
    if (
        not isinstance(error, dict)
        or error.get("retryable") is not True
        or error.get("code") not in gate.retry.retryable_error_codes
    ):
        return None
    details = error.get("details")
    status = details.get("runtime_status") if isinstance(details, dict) else None
    retry_after_ms = status.get("retry_after_ms") if isinstance(status, dict) else None
    requested = (
        float(retry_after_ms) / 1000
        if isinstance(retry_after_ms, (int, float)) and retry_after_ms >= 0
        else gate.retry.maximum_delay_seconds
    )
    return min(requested, gate.retry.maximum_delay_seconds)


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
