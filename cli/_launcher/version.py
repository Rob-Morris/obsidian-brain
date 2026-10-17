"""Typed ``brain.version`` launcher owner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from launcher_catalogue import LAUNCHER_CATALOGUE
from _version_contract import (
    CLI_FULL_SUPPORT_CORE_VERSION, CliVersionFacts, SEMVER_RE, parse_source_versions,
)

from .context import LauncherContext
from .contracts import Ok


@dataclass(frozen=True, slots=True)
class BrainVersionPayload:
    cli_version: str
    distributed_with_core_version: str
    full_support_core_version: str | None
    launcher_catalogue_schema: str
    launcher_catalogue_fingerprint: str
    launcher_command_count: int

    def __post_init__(self) -> None:
        versions = (self.cli_version, self.distributed_with_core_version)
        if self.full_support_core_version is not None:
            versions += (self.full_support_core_version,)
        for value in versions:
            if not SEMVER_RE.fullmatch(value):
                raise ValueError("brain.version metadata must contain semantic versions")
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


def read_cli_version_facts(source_root: Path) -> CliVersionFacts:
    """Read shipped declarations without selecting a Brain or asserting publication."""
    try:
        versions = parse_source_versions(
            core=(source_root / "src/brain-core/VERSION").read_text(encoding="utf-8"),
            unix_cli=(source_root / "cli/brain").read_text(encoding="utf-8"),
            windows_cli=(source_root / "cli/brain.cmd").read_text(encoding="utf-8"),
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"CLI version metadata is missing or corrupt: {exc}") from exc
    if CLI_FULL_SUPPORT_CORE_VERSION != "next-release" and not SEMVER_RE.fullmatch(CLI_FULL_SUPPORT_CORE_VERSION):
        raise ValueError("CLI full-support Core baseline is invalid")
    return CliVersionFacts(
        versions.cli_version, versions.brain_core_version,
        None if CLI_FULL_SUPPORT_CORE_VERSION == "next-release" else CLI_FULL_SUPPORT_CORE_VERSION,
    )


def version_result():
    facts = read_cli_version_facts(Path(__file__).resolve().parents[2])
    return Ok(
        BrainVersionRequest.COMMAND_ID,
        BrainVersionRequest.COMMAND_VERSION,
        BrainVersionPayload(
            facts.cli_version,
            facts.distributed_with_core_version,
            facts.full_support_core_version,
            LAUNCHER_CATALOGUE.schema,
            LAUNCHER_CATALOGUE.fingerprint,
            len(LAUNCHER_CATALOGUE.entries),
        ),
    )


def execute(context: LauncherContext, request: BrainVersionRequest):
    return version_result()


def version_text(payload: BrainVersionPayload) -> str:
    return (
        f"brain {payload.cli_version}\n"
        f"Distributed with Brain Core: {payload.distributed_with_core_version}\n"
        f"Full support from Brain Core: {payload.full_support_core_version or 'unknown (pending release)'}\n"
    )


def version_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainVersionRequest,
        BrainVersionPayload,
        "_launcher.version:version",
        execute,
    )
