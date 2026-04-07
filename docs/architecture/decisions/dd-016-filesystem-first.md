# DD-016: Filesystem-First Artefact Discovery

**Status:** Implemented (v0.5.0)

## Context

The compiled router needs to know which artefact types exist in the vault. This list could come from a central registry (a config file listing all types) or from scanning the filesystem (types are wherever their folders are).

## Decision

Artefact types are discovered by scanning vault folders, not by reading a registry. The convention: root-level non-system folders are living types; `_Temporal/` subfolders are temporal types. System folders are identified by a leading `_` or `.` — they are infrastructure, not artefact type containers. `_Temporal/` follows this convention but gets its own dedicated temporal scan for its child folders.

The type key is derived from the folder name (lowercase, spaces to hyphens). The taxonomy file for a type lives at `_Config/Taxonomy/{classification}/{type-key}.md`.

## Consequences

- Adding a new artefact type requires only creating its folder and taxonomy file — no registry to update.
- Renaming a type folder renames the type; tools detect the change on next compile.
- System folders (`_Archive/`, `.brain/`, etc.) are explicitly excluded from type discovery by naming convention.
- The filesystem is the authoritative list of types — there is no separate type registry that can fall out of sync.
