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
        "shaping.render",
        "shaping.start",
    ),
    "brain_check": ("vault.check",),
    "brain_classify": ("content.classify",),
    "brain_create": (
        "artefact.create",
        "resource.create",
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
        "document.patch",
        "document.update-frontmatter",
        "document.write",
    ),
    "brain_ingest": ("content.ingest",),
    # The retired readiness aggregate's published replacements form one
    # discovery/bootstrap closure; this is migration data, not a runtime alias.
    "brain_init": (
        "command.describe",
        "command.list",
        "runtime.status",
        "runtime.warmup",
        "session.start",
    ),
    "brain_list": (
        "artefact.list",
        "resource.list",
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
        "resource.read",
        "runtime.read-environment",
        "vault.read-file",
        "vault.read-router",
        "workspace.read",
    ),
    "brain_reparent": ("artefact.reparent",),
    "brain_resolve": ("content.resolve",),
    "brain_search": (
        "artefact.search",
        "resource.search",
    ),
    "brain_session": ("session.start",),
    "brain_set_key": ("artefact.set-key",),
    "brain_set_naming_field": ("artefact.set-naming-field",),
    "brain_set_status": ("artefact.set-status",),
    "brain_stage": ("stage.create",),
    "brain_upload_attachment": ("attachment.upload",),
}

_DOCUMENT_MUTATION_COMMANDS = (
    "document.edit",
    "document.patch",
    "document.update-frontmatter",
    "document.write",
)

# These names were previously broader than their spelling now implies. This is
# one-time authority projection: granting every replacement preserves the old
# capability without retaining a runtime alias or guessing at invocation data.
_PROFILE_EXPANSIONS = {
    "document.edit": _DOCUMENT_MUTATION_COMMANDS,
    **{
        f"{resource}.edit": _DOCUMENT_MUTATION_COMMANDS
        for resource in ("artefact", "memory", "skill", "style", "template")
    },
}


_REMOVED_GRANULAR_COMMANDS = {
    **{
        f"{resource}.{operation}": (
            "document.write"
            if operation in ("append", "prepend")
            else "document.patch"
            if operation == "replace-text"
            else "document.edit"
        )
        for resource in ("artefact", "memory", "skill", "style", "template")
        for operation in ("append", "delete-section", "prepend", "replace-text")
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
    **{
        f"{resource}.create": "resource.create"
        for resource in ("memory", "skill", "style", "template")
    },
    **{
        f"{resource}.list": "resource.list"
        for resource in (
            "memory",
            "plugin",
            "skill",
            "style",
            "template",
            "trigger",
            "type",
        )
    },
    **{
        f"{resource}.read": "resource.read"
        for resource in (
            "memory",
            "plugin",
            "skill",
            "style",
            "template",
            "trigger",
            "type",
        )
    },
    **{
        f"{resource}.search": "resource.search"
        for resource in ("memory", "plugin", "skill", "style", "trigger")
    },
    "shaping.render-presentation": "shaping.render",
    "shaping.render-printable": "shaping.render",
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
    exact_previous_granular_set = _is_exact_previous_granular_builtin_set(
        profiles,
        granular_entries,
        builtins,
    )
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
        if (exact_builtin_set or exact_previous_granular_set) and profile in builtins:
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
                expansion = _PROFILE_EXPANSIONS.get(tool)
                if expansion is not None:
                    command_ids.update(expansion)
                    continue
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


def _is_exact_previous_granular_builtin_set(
    profiles: Mapping[str, object],
    granular_entries: Mapping[str, object],
    builtins: Mapping[str, tuple[str, ...]],
) -> bool:
    """Recognise the exact v0.55 shipped profiles after command consolidation.

    The previous built-ins differ from the current ones only by superseded
    target-only leaves and newly added bootstrap/access controls. Comparing
    their projected meaning avoids embedding five large duplicate allow-lists.
    """

    if set(profiles) != set(builtins):
        return False
    new_access_tools = {
        "access.reduce",
        "access.request",
        "access.status",
    }
    new_runtime_tools = {
        "runtime.status",
        "runtime.warmup",
    }
    new_document_tools = {
        "document.patch",
        "document.update-frontmatter",
        "document.write",
    }
    for additions in (
        set(),
        new_document_tools,
        new_access_tools,
        new_access_tools | new_runtime_tools,
        new_access_tools | new_document_tools,
        new_access_tools | new_runtime_tools | new_document_tools,
    ):
        matches = True
        for profile, expected in builtins.items():
            definition = profiles.get(profile)
            if not isinstance(definition, Mapping):
                return False
            allow = definition.get("allow")
            if not isinstance(allow, list) or any(
                not isinstance(item, str) or not item.strip() for item in allow
            ):
                return False
            try:
                projected = _project_tool_set(allow, granular_entries)
            except ProfileMigrationError:
                return False
            if projected != set(expected) - additions:
                matches = False
                break
        if matches:
            return True
    return False


def _project_tool_set(
    tools: tuple[str, ...] | list[str],
    granular_entries: Mapping[str, object],
) -> set[str]:
    """Project old tool names to the current command-tool vocabulary."""

    projected = set()
    for tool in tools:
        expansion = _PROFILE_EXPANSIONS.get(tool)
        if expansion is not None:
            projected.update(expansion)
            continue
        if tool in granular_entries:
            projected.add(tool)
            continue
        consolidated = _REMOVED_GRANULAR_COMMANDS.get(tool)
        if consolidated is not None:
            projected.add(consolidated)
            continue
        replacements = _LEGACY_COMMANDS.get(tool)
        if replacements is None:
            raise ProfileMigrationError(f"unknown tool '{tool}'")
        projected.update(replacements)
    return projected


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
