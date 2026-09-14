"""Explicit dynamic adapter resolution into sealed typed requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping

from .requests import CommandRequest, command_identity
from .identity import REQUEST_IDENTITY_FIELDS
from .types import validate_command_id


class ResolutionErrorCode(str, Enum):
    UNKNOWN_COMMAND = "unknown_command"
    UNSUPPORTED_COMMAND_VERSION = "unsupported_command_version"
    INVALID_REQUEST = "invalid_request"


class RequestResolutionError(ValueError):
    def __init__(self, code: ResolutionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


Decoder = Callable[[Mapping[str, object]], CommandRequest]


@dataclass(frozen=True, slots=True)
class ResolverEntry:
    request_type: type
    decoder: Decoder

    @property
    def command_id(self) -> str:
        return self.request_type.COMMAND_ID

    @property
    def command_version(self) -> int:
        return self.request_type.COMMAND_VERSION

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1:
            raise ValueError("resolver command version must be positive")


@dataclass(frozen=True, slots=True)
class RequestResolver:
    entries: tuple[ResolverEntry, ...]

    def __post_init__(self) -> None:
        ids = [entry.command_id for entry in self.entries]
        if ids != sorted(ids):
            raise ValueError("resolver entries must be sorted by command_id")
        if len(ids) != len(set(ids)):
            raise ValueError("resolver command identifiers must be unique")

    def resolve(
        self,
        command_id: str,
        payload: Mapping[str, object],
        *,
        expected_version: int | None = None,
    ) -> CommandRequest:
        """Resolve transport data; local callers omit expected_version.

        Command identity and version are resolver metadata, not fields that a
        caller may smuggle into semantic input. Serialised/remote adapters can
        negotiate an expected version separately and fail before execution.
        """

        validate_command_id(command_id)
        if any(name in payload for name in REQUEST_IDENTITY_FIELDS):
            raise RequestResolutionError(
                ResolutionErrorCode.INVALID_REQUEST,
                "command identity and version are transport metadata, not request fields",
            )
        entry = next((item for item in self.entries if item.command_id == command_id), None)
        if entry is None:
            raise RequestResolutionError(
                ResolutionErrorCode.UNKNOWN_COMMAND,
                f"unknown command: {command_id}",
            )
        if expected_version is not None and expected_version != entry.command_version:
            raise RequestResolutionError(
                ResolutionErrorCode.UNSUPPORTED_COMMAND_VERSION,
                f"{command_id} requires command version {entry.command_version}",
            )
        try:
            request = entry.decoder(payload)
        except RequestResolutionError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise RequestResolutionError(
                ResolutionErrorCode.INVALID_REQUEST,
                f"invalid request for {command_id}: {exc}",
            ) from exc
        if type(request) is not entry.request_type:
            raise RequestResolutionError(
                ResolutionErrorCode.INVALID_REQUEST,
                f"decoder for {command_id} returned the wrong request type",
            )
        identity, version, _result_type = command_identity(request)
        if (identity, version) != (entry.command_id, entry.command_version):
            raise RequestResolutionError(
                ResolutionErrorCode.INVALID_REQUEST,
                f"decoder for {command_id} returned mismatched identity",
            )
        return request
