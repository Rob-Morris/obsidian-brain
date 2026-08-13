"""Strict transport-payload decoding primitives."""

from __future__ import annotations

from typing import Mapping


def reject_unexpected(
    payload: Mapping[str, object],
    allowed: set[str],
    *,
    label: str = "fields",
) -> None:
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"unexpected {label}: {', '.join(unexpected)}")


def decode_empty(payload: Mapping[str, object], request_type):
    reject_unexpected(payload, set())
    return request_type()


def optional_string(value: object, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    return value


def optional_bool(value: object, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def decode_bool(
    payload: Mapping[str, object],
    field: str,
    *,
    default: bool = False,
) -> bool:
    value = payload.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value
