"""One-time legacy profile projection for the coordinated command cutover."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from _application.catalogue import ApplicationCatalogue
from _application.projection import project_identity
from _application.types import RetryClass

from .profiles import builtin_profile_allow_lists


_LEGACY_BUILTIN_ALLOW = {
    "reader": (
        "brain_init",
        "brain_session",
        "brain_read",
        "brain_outline",
        "brain_check",
        "brain_search",
        "brain_list",
        "brain_classify",
        "brain_resolve",
    ),
    "contributor": (
        "brain_init",
        "brain_session",
        "brain_read",
        "brain_outline",
        "brain_check",
        "brain_search",
        "brain_list",
        "brain_classify",
        "brain_resolve",
        "brain_stage",
        "brain_discard_stage",
        "brain_upload_attachment",
        "brain_create",
        "brain_edit",
        "brain_reparent",
        "brain_set_status",
        "brain_set_key",
        "brain_set_naming_field",
        "brain_ingest",
    ),
    "operator": (
        "brain_init",
        "brain_session",
        "brain_read",
        "brain_outline",
        "brain_check",
        "brain_search",
        "brain_list",
        "brain_classify",
        "brain_resolve",
        "brain_stage",
        "brain_discard_stage",
        "brain_upload_attachment",
        "brain_create",
        "brain_edit",
        "brain_reparent",
        "brain_set_status",
        "brain_set_key",
        "brain_set_naming_field",
        "brain_ingest",
        "brain_define",
        "brain_move",
        "brain_action",
    ),
}

_LEGACY_COMMANDS = {
    "brain_action": (
        "artefact.delete",
        "artefact.reparent-children",
        "links.fix",
        "shaping.render-presentation",
        "shaping.render-printable",
        "shaping.start",
    ),
    "brain_check": ("vault.check",),
    "brain_classify": ("content.classify",),
    "brain_create": (
        "artefact.create",
        "memory.create",
        "skill.create",
        "style.create",
        "template.create",
    ),
    "brain_define": (
        "plugin.create",
        "plugin.replace",
        "trigger.create",
        "trigger.delete",
        "trigger.replace",
        "type.create",
        "type.replace",
    ),
    "brain_discard_stage": ("stage.discard",),
    "brain_edit": (
        "document.edit",
    ),
    "brain_ingest": ("content.ingest",),
    # The retired readiness aggregate's published replacements form one
    # discovery/bootstrap closure; this is migration data, not a runtime alias.
    "brain_init": ("command.describe", "command.list", "session.start"),
    "brain_list": (
        "artefact.list",
        "memory.list",
        "plugin.list",
        "skill.list",
        "style.list",
        "template.list",
        "trigger.list",
        "type.list",
        "workspace.list",
    ),
    "brain_move": (
        "artefact.archive",
        "artefact.convert",
        "artefact.rename",
        "artefact.unarchive",
    ),
    "brain_outline": ("artefact.outline",),
    "brain_read": (
        "artefact.read",
        "memory.read",
        "plugin.read",
        "runtime.read-environment",
        "skill.read",
        "style.read",
        "template.read",
        "trigger.read",
        "type.read",
        "vault.read-file",
        "vault.read-router",
        "workspace.read",
    ),
    "brain_reparent": ("artefact.reparent",),
    "brain_resolve": ("content.resolve",),
    "brain_search": (
        "artefact.search",
        "memory.search",
        "plugin.search",
        "skill.search",
        "style.search",
        "trigger.search",
    ),
    "brain_session": ("session.start",),
    "brain_set_key": ("artefact.set-key",),
    "brain_set_naming_field": ("artefact.set-naming-field",),
    "brain_set_status": ("artefact.set-status",),
    "brain_stage": ("stage.create",),
    "brain_upload_attachment": ("attachment.upload",),
}


_REMOVED_GRANULAR_COMMANDS = {
    **{
        f"{resource}.{operation}": "document.edit"
        for resource in ("artefact", "memory", "skill", "style", "template")
        for operation in ("append", "delete-section", "edit", "prepend", "replace-text")
    },
    "artefact.list-archived": "artefact.list",
    "artefact.read-archived": "artefact.read",
    "artefact.repair-frontmatter": "artefact.repair",
    "artefact.repair-ownership": "artefact.repair",
    "retrieval.rebuild-lexical": "retrieval.refresh-lexical",
    "retrieval.repair-lexical": "retrieval.refresh-lexical",
    "runtime.rebuild-router": "runtime.refresh-router",
    "runtime.repair-router": "runtime.refresh-router",
    "type.install": "type.sync",
    "workspace.resolve": "workspace.read",
}


class ProfileMigrationError(ValueError):
    """A profile cannot be migrated without changing unknown authority."""


@dataclass(frozen=True, slots=True)
class ProfileMigrationChange:
    profile: str
    strategy: str
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfileMigrationResult:
    profiles: dict[str, dict[str, object]]
    changes: tuple[ProfileMigrationChange, ...]


def migrate_profile_allow_lists(
    profiles: Mapping[str, object],
    catalogue: ApplicationCatalogue,
) -> ProfileMigrationResult:
    """Project raw shared profile definitions once; never provide runtime fallback."""

    granular_entries = {
        project_identity(entry.command_id).mcp_tool: entry
        for entry in catalogue.entries
    }
    entries_by_command = {
        entry.command_id: entry
        for entry in catalogue.entries
    }
    _validate_migration_map(granular_entries)
    builtins = builtin_profile_allow_lists(catalogue)
    exact_builtin_set = _is_exact_legacy_builtin_set(profiles)
    source_profiles = (
        {
            profile: profiles.get(
                profile,
                {"label": f"{profile} profile", "allow": []},
            )
            for profile in builtins
        }
        if exact_builtin_set
        else dict(profiles)
    )
    migrated = {}
    changes = []
    for profile, raw_definition in source_profiles.items():
        if not isinstance(profile, str) or not profile.strip():
            raise ProfileMigrationError("profile names must be non-empty strings")
        if not isinstance(raw_definition, Mapping):
            raise ProfileMigrationError(f"profile '{profile}' must be a mapping")
        definition = dict(raw_definition)
        before_raw = definition.get("allow", [])
        if not isinstance(before_raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in before_raw
        ):
            raise ProfileMigrationError(
                f"profile '{profile}' allow-list must contain only non-empty strings"
            )
        before = tuple(before_raw)
        if exact_builtin_set and profile in builtins:
            after = builtins[profile]
            strategy = "builtin"
        elif profile in _LEGACY_BUILTIN_ALLOW and set(before) == set(
            _LEGACY_BUILTIN_ALLOW[profile]
        ):
            after = builtins[profile]
            strategy = "builtin"
        else:
            command_ids = set()
            for tool in before:
                if tool in granular_entries:
                    command_ids.add(granular_entries[tool].command_id)
                    continue
                consolidated = _REMOVED_GRANULAR_COMMANDS.get(tool)
                if consolidated is not None:
                    command_ids.add(consolidated)
                    continue
                replacements = _LEGACY_COMMANDS.get(tool)
                if replacements is None:
                    raise ProfileMigrationError(
                        f"profile '{profile}' contains unknown tool '{tool}'"
                    )
                command_ids.update(replacements)
            if any(
                entries_by_command[command_id].retry_class
                is RetryClass.RECEIPT_REQUIRED
                for command_id in command_ids
            ):
                command_ids.add("invocation.read")
            after = tuple(
                sorted(project_identity(command_id).mcp_tool for command_id in command_ids)
            )
            strategy = "custom"
        definition["allow"] = list(after)
        migrated[profile] = definition
        if before != after:
            changes.append(ProfileMigrationChange(profile, strategy, before, after))
    return ProfileMigrationResult(migrated, tuple(changes))


def _is_exact_legacy_builtin_set(profiles: Mapping[str, object]) -> bool:
    if set(profiles) != set(_LEGACY_BUILTIN_ALLOW):
        return False
    for profile, expected in _LEGACY_BUILTIN_ALLOW.items():
        definition = profiles.get(profile)
        if not isinstance(definition, Mapping):
            return False
        allow = definition.get("allow")
        if not isinstance(allow, list) or set(allow) != set(expected):
            return False
    return True


def _validate_migration_map(granular_entries: Mapping[str, object]) -> None:
    malformed = sorted(
        legacy_tool
        for legacy_tool, command_ids in _LEGACY_COMMANDS.items()
        if command_ids != tuple(sorted(set(command_ids)))
    )
    if malformed:
        raise ProfileMigrationError(
            "legacy profile migration targets must be sorted and unique: "
            + ", ".join(malformed)
        )
    command_tools = {
        project_identity(command_id).mcp_tool
        for command_ids in _LEGACY_COMMANDS.values()
        for command_id in command_ids
    }
    missing = sorted(command_tools - set(granular_entries))
    missing.extend(
        sorted(set(_REMOVED_GRANULAR_COMMANDS.values()) - set(granular_entries))
    )
    if missing:
        raise ProfileMigrationError(
            "legacy profile migration references unavailable commands: "
            + ", ".join(sorted(set(missing)))
        )
