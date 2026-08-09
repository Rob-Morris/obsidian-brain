"""Typed ``brain.version`` launcher owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from launcher_catalogue import LAUNCHER_CATALOGUE

from .context import LauncherContext
from .contracts import Ok


@dataclass(frozen=True, slots=True)
class BrainVersionPayload:
    cli_version: str
    launcher_catalogue_schema: str
    launcher_catalogue_fingerprint: str
    launcher_command_count: int

    def __post_init__(self) -> None:
        if not self.cli_version.strip():
            raise ValueError("brain.version CLI version must be non-empty")
        if not self.launcher_catalogue_schema.startswith("brain.launcher-catalogue/"):
            raise ValueError("brain.version launcher catalogue schema is invalid")
        if not self.launcher_catalogue_fingerprint.startswith("sha256:"):
            raise ValueError("brain.version launcher fingerprint is invalid")
        if self.launcher_command_count < 1:
            raise ValueError("brain.version launcher command count must be positive")


@dataclass(frozen=True, slots=True)
class BrainVersionRequest:
    COMMAND_ID: ClassVar[str] = "brain.version"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainVersionPayload


def execute(context: LauncherContext, request: BrainVersionRequest):
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainVersionPayload(
            context.cli_version,
            LAUNCHER_CATALOGUE.schema,
            LAUNCHER_CATALOGUE.fingerprint,
            len(LAUNCHER_CATALOGUE.entries),
        ),
    )


def version_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainVersionRequest,
        BrainVersionPayload,
        "_launcher.version:version",
        execute,
    )
