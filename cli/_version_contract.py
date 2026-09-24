"""Pure parser and coherence model for shipped Brain component versions."""

from __future__ import annotations

import re
from dataclasses import dataclass


SEMVER_PATTERN = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
SEMVER_RE = re.compile(rf"^{SEMVER_PATTERN}$")
_SUMMARY_VERSION_SUFFIX = re.compile(
    rf"\s+(?:as\s+)?\(?v{SEMVER_PATTERN}\)?$",
    re.IGNORECASE,
)


def release_summary_problems(summary: str) -> tuple[str, ...]:
    """Return the canonical Summary rules shared by release preparation and contracts."""
    problems: list[str] = []
    if summary.endswith("."):
        problems.append("Summary must not end with a period")
    if _SUMMARY_VERSION_SUFFIX.search(summary):
        problems.append("Summary must not carry a version suffix")
    return tuple(problems)


@dataclass(frozen=True, slots=True)
class SourceVersions:
    cli_version: str
    brain_core_version: str


@dataclass(frozen=True, slots=True)
class VersionContract:
    core: str | None
    readme: str | None
    cli_unix: str | None
    cli_windows: str | None
    install_ref_unix: str | None
    install_ref_windows: str | None
    documented_cli_unix: str | None
    documented_cli_windows: str | None
    documented_cli_declaration: str | None
    documented_install_ref: str | None
    user_reference_core: str | None
    user_reference_cli: str | None
    proxy: str | None
    changelog_head: str | None
    coherent: bool
    declaration_counts: tuple[tuple[str, int], ...]

    def count(self, name: str) -> int:
        return dict(self.declaration_counts).get(name, 0)


def _declaration(
    pattern: str,
    text: str | None,
    *,
    flags: int = 0,
) -> tuple[str | None, int]:
    matches = re.findall(pattern, text or "", flags)
    return (matches[0] if len(matches) == 1 else None, len(matches))


def parse_version_contract(
    *,
    core: str | None,
    readme: str | None = None,
    unix_cli: str | None = None,
    windows_cli: str | None = None,
    proxy: str | None = None,
    changelog: str | None = None,
    functional_cli: str | None = None,
    user_reference: str | None = None,
) -> VersionContract:
    """Parse all canonical release declarations without performing I/O."""

    core_version = core.strip() if core and SEMVER_RE.fullmatch(core.strip()) else None
    declarations = {
        "readme": _declaration(rf"version-({SEMVER_PATTERN})-blue", readme),
        "cli_unix": _declaration(
            rf'^BRAIN_CLI_VERSION="({SEMVER_PATTERN})"$', unix_cli, flags=re.MULTILINE
        ),
        "cli_windows": _declaration(
            rf'^set "BRAIN_CLI_VERSION=({SEMVER_PATTERN})"$',
            windows_cli,
            flags=re.MULTILINE | re.IGNORECASE,
        ),
        "install_ref_unix": _declaration(
            rf'^BRAIN_INSTALL_REF="v({SEMVER_PATTERN})"$',
            unix_cli,
            flags=re.MULTILINE,
        ),
        "install_ref_windows": _declaration(
            rf'^set "BRAIN_INSTALL_REF=v({SEMVER_PATTERN})"$',
            windows_cli,
            flags=re.MULTILINE | re.IGNORECASE,
        ),
        "documented_cli_unix": _declaration(
            rf"lib/brain-cli/({SEMVER_PATTERN})/", functional_cli
        ),
        "documented_cli_windows": _declaration(
            rf"lib\\brain-cli\\({SEMVER_PATTERN})\\", functional_cli
        ),
        "documented_cli_declaration": _declaration(
            rf"`BRAIN_CLI_VERSION` is `({SEMVER_PATTERN})`", functional_cli
        ),
        "documented_install_ref": _declaration(
            rf"`BRAIN_INSTALL_REF` is `v({SEMVER_PATTERN})`", functional_cli
        ),
        "proxy": _declaration(
            rf'^PROXY_VERSION = "({SEMVER_PATTERN})"$', proxy, flags=re.MULTILINE
        ),
    }
    references = re.findall(
        rf"Brain Core ({SEMVER_PATTERN}) and CLI ({SEMVER_PATTERN})",
        user_reference or "",
    )
    user_core, user_cli = references[0] if len(references) == 1 else (None, None)
    changelog_rows = re.findall(
        rf"^\| \[v({SEMVER_PATTERN})\]\(changelog/v[^)]+\)",
        changelog or "",
        re.MULTILINE,
    )
    values = {name: declaration[0] for name, declaration in declarations.items()}
    counts = tuple(
        sorted(
            ((name, declaration[1]) for name, declaration in declarations.items()),
        )
    ) + (("user_reference", len(references)),)
    required = (
        core_version,
        values["readme"],
        values["cli_unix"],
        values["cli_windows"],
        values["install_ref_unix"],
        values["install_ref_windows"],
        values["documented_cli_unix"],
        values["documented_cli_windows"],
        values["documented_cli_declaration"],
        values["documented_install_ref"],
        user_core,
        user_cli,
        values["proxy"],
    )
    changelog_head = changelog_rows[0] if changelog_rows else None
    coherent = (
        all(value is not None for value in required)
        and core_version
        == values["readme"]
        == values["install_ref_unix"]
        == values["install_ref_windows"]
        == values["documented_install_ref"]
        == user_core
        == changelog_head
        and values["cli_unix"]
        == values["cli_windows"]
        == values["documented_cli_unix"]
        == values["documented_cli_windows"]
        == values["documented_cli_declaration"]
        == user_cli
    )
    return VersionContract(
        core_version,
        values["readme"],
        values["cli_unix"],
        values["cli_windows"],
        values["install_ref_unix"],
        values["install_ref_windows"],
        values["documented_cli_unix"],
        values["documented_cli_windows"],
        values["documented_cli_declaration"],
        values["documented_install_ref"],
        user_core,
        user_cli,
        values["proxy"],
        changelog_head,
        coherent,
        counts,
    )


def parse_source_versions(
    *, core: str, unix_cli: str, windows_cli: str
) -> SourceVersions:
    """Return the one internally coherent Core/CLI source pair or fail closed."""

    parsed = parse_version_contract(
        core=core,
        unix_cli=unix_cli,
        windows_cli=windows_cli,
    )
    if parsed.count("cli_unix") != 1 or parsed.count("cli_windows") != 1:
        raise ValueError("platform CLI bootloaders do not declare one matching version")
    if parsed.cli_unix != parsed.cli_windows:
        raise ValueError("platform CLI bootloaders do not declare one matching version")
    if (
        parsed.count("install_ref_unix") != 1
        or parsed.count("install_ref_windows") != 1
        or parsed.core is None
        or parsed.install_ref_unix != parsed.core
        or parsed.install_ref_windows != parsed.core
    ):
        raise ValueError("platform CLI install refs do not match Brain Core VERSION")
    return SourceVersions(parsed.cli_unix, parsed.core)
