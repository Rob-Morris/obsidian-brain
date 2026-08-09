"""Typed machine-global operator utility owners."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import ClassVar

from .context import LauncherContext
from .contracts import Ok


_KEY = re.compile(r"^[a-z]+-[a-z]+-[a-z]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class OperatorKeyCandidate:
    key: str
    sha256: str

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key) or not _SHA256.fullmatch(self.sha256):
            raise ValueError("operator key candidates require key and SHA-256")


@dataclass(frozen=True, slots=True)
class OperatorGenerateKeyPayload:
    candidates: tuple[OperatorKeyCandidate, ...]

    def __post_init__(self) -> None:
        if not self.candidates or any(
            not isinstance(candidate, OperatorKeyCandidate)
            for candidate in self.candidates
        ):
            raise ValueError("operator.generate-key requires typed candidates")


@dataclass(frozen=True, slots=True)
class OperatorGenerateKeyRequest:
    COMMAND_ID: ClassVar[str] = "operator.generate-key"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = OperatorGenerateKeyPayload

    count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.count, int) or isinstance(self.count, bool):
            raise ValueError("operator key count must be an integer")
        if not 1 <= self.count <= 20:
            raise ValueError("operator key count must be between 1 and 20")


def execute_generate_key(
    _context: LauncherContext,
    request: OperatorGenerateKeyRequest,
):
    import generate_key

    candidates = tuple(
        OperatorKeyCandidate(material.key, material.sha256)
        for material in (
            generate_key.generate_key_material() for _index in range(request.count)
        )
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        OperatorGenerateKeyPayload(candidates),
    )


def generate_key_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        OperatorGenerateKeyRequest,
        OperatorGenerateKeyPayload,
        "_launcher.operator:generate_key",
        execute_generate_key,
    )
