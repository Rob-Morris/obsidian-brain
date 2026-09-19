"""Fail-closed inventory and acknowledgement for the global CLI cutover."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


CUTOVER_VERSION = (0, 55, 0)
REGISTRY_SCHEMA_MAX = 2
_HEADER = re.compile(r"^# brain registry v([0-9]+)\b")
_BRAIN_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class CutoverBrain:
    brain_id: str
    vault_root: str
    brain_core_version: str
    selected: bool
    requires_recovery_cli: bool
    direct_user_writer_supported: bool = False


@dataclass(frozen=True, slots=True)
class CutoverPreflight:
    registry_path: str
    registry_complete: bool
    selected_brain_ids: tuple[str, ...]
    classified_brains: tuple[CutoverBrain, ...]
    affected_brain_ids: tuple[str, ...]
    excluded_stale_brain_ids: tuple[str, ...]
    remote_brain_ids: tuple[str, ...]
    source_brain_core_version: str
    old_cli_version: str
    new_cli_version: str
    interface_epoch: int
    proxy_protocol: int
    removed_surfaces: tuple[str, ...]
    restart_actions: tuple[str, ...]


class CutoverPreflightError(ValueError):
    pass


def preflight(
    *,
    selected_vault: Path,
    source_brain_core_version: str,
    old_cli_version: str,
    new_cli_version: str,
    interface_epoch: int,
    proxy_protocol: int,
    acknowledge_global_cli_cutover: bool,
    excluded_stale_brain_ids: tuple[str, ...],
) -> CutoverPreflight:
    """Classify the complete registry before any Brain or CLI mutation."""

    import vault_registry

    registry = Path(vault_registry.registry_path())
    _validate_registry_schema(registry)
    try:
        entries = vault_registry.load_registry_entries()
    except (OSError, ValueError, vault_registry.RegistryReadError) as exc:
        raise CutoverPreflightError(f"authoritative Brain registry is unreadable: {exc}") from exc
    selected = selected_vault.resolve()
    classified: list[CutoverBrain] = []
    stale: list[str] = []
    remote: list[str] = []
    paths: dict[Path, str] = {}
    source_version = _version(source_brain_core_version, "source Brain Core")
    if source_version < CUTOVER_VERSION:
        raise CutoverPreflightError(
            "the coordinated cutover requires Brain Core 0.55.0 or newer"
        )
    _version(old_cli_version, "installed Brain CLI")
    if _version(new_cli_version, "source Brain CLI") < (2, 0, 0):
        raise CutoverPreflightError(
            "the coordinated cutover requires Brain CLI 2.0.0 or newer"
        )
    for brain_id, entry in sorted(entries.items()):
        if entry.kind == vault_registry.TYPE_REMOTE:
            remote.append(brain_id)
            continue
        if entry.kind != vault_registry.TYPE_LOCAL:
            raise CutoverPreflightError(
                f"registry entry {brain_id!r} has unsupported kind {entry.kind!r}"
            )
        root = Path(entry.value).expanduser()
        if not root.is_absolute() or root.is_symlink():
            raise CutoverPreflightError(
                f"registry entry {brain_id!r} has an unsafe local path"
            )
        resolved = root.resolve()
        previous = paths.get(resolved)
        if previous is not None:
            raise CutoverPreflightError(
                f"registry IDs {previous!r} and {brain_id!r} resolve to the same Brain"
            )
        paths[resolved] = brain_id
        version_path = resolved / ".brain-core" / "VERSION"
        try:
            version_text = version_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            stale.append(brain_id)
            continue
        except OSError as exc:
            raise CutoverPreflightError(
                f"Brain {brain_id!r} version is unreadable: {exc}"
            ) from exc
        parsed = _version(version_text, f"Brain {brain_id!r}")
        if parsed > source_version:
            raise CutoverPreflightError(
                f"Brain {brain_id!r} is newer than this CLI distribution ({version_text})"
            )
        classified.append(
            CutoverBrain(
                brain_id,
                str(resolved),
                version_text,
                resolved == selected,
                parsed < CUTOVER_VERSION,
                parsed >= (0, 70, 0),
            )
        )
    exclusions = tuple(sorted(set(excluded_stale_brain_ids)))
    if exclusions != excluded_stale_brain_ids:
        raise CutoverPreflightError("stale Brain exclusions must be sorted and unique")
    unknown_exclusions = sorted(set(exclusions) - set(stale))
    if unknown_exclusions:
        raise CutoverPreflightError(
            "stale Brain exclusions are not stale registry entries: "
            + ", ".join(unknown_exclusions)
        )
    unclassified = sorted(set(stale) - set(exclusions))
    if unclassified:
        raise CutoverPreflightError(
            "stale registry entries require explicit exclusion before cutover: "
            + ", ".join(unclassified)
        )
    selected_ids = tuple(item.brain_id for item in classified if item.selected)
    if not selected_ids:
        raise CutoverPreflightError(
            "the selected Brain is not present in the complete local registry"
        )
    affected = tuple(
        item.brain_id
        for item in classified
        if not item.selected and item.requires_recovery_cli
    )
    if affected and not acknowledge_global_cli_cutover:
        raise CutoverPreflightError(
            "global CLI 2 replacement affects pre-cutover Brains; acknowledge these IDs: "
            + ", ".join(affected)
        )
    return CutoverPreflight(
        str(registry),
        True,
        selected_ids,
        tuple(classified),
        affected,
        exclusions,
        tuple(remote),
        source_brain_core_version,
        old_cli_version,
        new_cli_version,
        interface_epoch,
        proxy_protocol,
        (
            "aggregate MCP tools",
            "CLI v1 flat dispatch",
            "legacy direct-script aliases",
            "public aggregate Python wrappers",
        ),
        (
            "Restart every MCP client using the upgraded Brain.",
            "Re-discover tools before the next MCP call.",
            "Use brain upgrade for each acknowledged pre-cutover Brain.",
            "Use the installed machine CLI for user MCP changes; pre-0.70 direct user-registration writers are unsupported after migration.",
        ),
    )


def _validate_registry_schema(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    except (OSError, UnicodeError) as exc:
        raise CutoverPreflightError(f"authoritative Brain registry is unreadable: {exc}") from exc
    if not lines:
        return
    match = _HEADER.match(lines[0])
    if match is not None and int(match.group(1)) > REGISTRY_SCHEMA_MAX:
        raise CutoverPreflightError(
            f"Brain registry schema v{match.group(1)} is newer than supported v{REGISTRY_SCHEMA_MAX}"
        )
    for number, line in enumerate(lines, 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) not in {2, 3} or not _BRAIN_ID.fullmatch(fields[0]):
            raise CutoverPreflightError(
                f"authoritative Brain registry is unreadable: malformed line {number}"
            )


def _version(value: str, label: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise CutoverPreflightError(f"{label} has an unsupported version: {value!r}")
    return tuple(int(part) for part in parts)
