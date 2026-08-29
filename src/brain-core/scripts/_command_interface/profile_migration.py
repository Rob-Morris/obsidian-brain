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

_DOCUMENT_MUTATION_COMMANDS = (
    "document.replace-text",
    "document.structured-edit",
    "document.update-frontmatter",
    "document.write-body",
)

_PREVIOUS_DOCUMENT_COMMANDS = {
    "document.edit": "document.structured-edit",
    "document.patch": "document.replace-text",
    "document.write": "document.write-body",
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
    "brain_edit": _DOCUMENT_MUTATION_COMMANDS,
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
    **_PREVIOUS_DOCUMENT_COMMANDS,
    **{
        f"{resource}.{operation}": (
            "document.write-body"
            if operation in ("append", "prepend")
            else "document.replace-text"
            if operation == "replace-text"
            else "document.structured-edit"
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
    legacy_builtins_present = _contains_exact_legacy_builtins(profiles)
    previous_granular_builtins_present = _contains_exact_previous_granular_builtins(
        profiles,
        granular_entries,
        builtins,
    )
    source_profiles = dict(profiles)
    builtin_profiles_to_project = set()
    if legacy_builtins_present:
        builtin_profiles_to_project.update(_LEGACY_BUILTIN_ALLOW)
        for profile in builtins:
            if profile not in source_profiles:
                source_profiles[profile] = {
                    "label": f"{profile} profile",
                    "allow": [],
                }
                builtin_profiles_to_project.add(profile)
    elif previous_granular_builtins_present:
        builtin_profiles_to_project.update(builtins)
    if legacy_builtins_present or previous_granular_builtins_present:
        source_profiles = {
            **{
                profile: source_profiles[profile]
                for profile in builtins
                if profile in source_profiles
            },
            **{
                profile: definition
                for profile, definition in source_profiles.items()
                if profile not in builtins
            },
        }
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
        if profile in builtin_profiles_to_project:
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


def migrate_current_document_command_names(
    profiles: Mapping[str, object],
    catalogue: ApplicationCatalogue,
) -> ProfileMigrationResult:
    """Rename the v0.62.8 document grants without widening their authority.

    ``document.edit`` had broad semantics in an earlier granular catalogue, so
    the historical migration deliberately expands it. By v0.62.8 the same
    spelling meant only structural editing. This version-specific migration
    therefore owns the unambiguous one-to-one cutover instead of adding a
    runtime alias or guessing inside the historical projection.
    """

    current_tools = {
        project_identity(entry.command_id).mcp_tool for entry in catalogue.entries
    }
    migrated = {}
    changes = []
    for profile, raw_definition in profiles.items():
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
        after_list = []
        for tool in before:
            replacement = _PREVIOUS_DOCUMENT_COMMANDS.get(tool, tool)
            if replacement not in current_tools:
                raise ProfileMigrationError(
                    f"profile '{profile}' contains unknown tool '{tool}'"
                )
            if replacement not in after_list:
                after_list.append(replacement)
        after = tuple(after_list)
        definition["allow"] = list(after)
        migrated[profile] = definition
        if before != after:
            changes.append(ProfileMigrationChange(profile, "rename", before, after))
    return ProfileMigrationResult(migrated, tuple(changes))


def _contains_exact_legacy_builtins(profiles: Mapping[str, object]) -> bool:
    if not set(_LEGACY_BUILTIN_ALLOW) <= set(profiles):
        return False
    for profile, expected in _LEGACY_BUILTIN_ALLOW.items():
        definition = profiles.get(profile)
        if not isinstance(definition, Mapping):
            return False
        allow = definition.get("allow")
        if not isinstance(allow, list) or set(allow) != set(expected):
            return False
    return True


def _contains_exact_previous_granular_builtins(
    profiles: Mapping[str, object],
    granular_entries: Mapping[str, object],
    builtins: Mapping[str, tuple[str, ...]],
) -> bool:
    """Recognise the exact v0.55 shipped profiles after command consolidation.

    The previous built-ins differ from the current ones only by superseded
    target-only leaves and newly added bootstrap/access controls. Comparing
    their projected meaning avoids embedding five large duplicate allow-lists.
    """

    if not set(builtins) <= set(profiles):
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
        "document.replace-text",
        "document.update-frontmatter",
        "document.write-body",
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
