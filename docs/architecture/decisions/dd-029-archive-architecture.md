# DD-029: Archive architecture

**Status:** Implemented

## Context

Artefacts have a lifecycle: they begin living, pass through active statuses, and eventually reach a terminal status (e.g., `implemented`, `superseded`, `cancelled`). Once terminal, they are candidates for archival. The question is where archived artefacts go and how they are stored.

Requirements:
- Archived artefacts must be excluded from normal search, listing, and compliance checks without requiring per-query filtering logic.
- The archive must be browsable — vault operators need to find old artefacts.
- Wikilinks to archived artefacts should continue to work.
- The archive path must be unambiguous so code can detect archived status with a simple path check.

## Decision

Archived artefacts are moved to `_Archive/{TypeFolder}/{project}/` with a `yyyymmdd-` date prefix prepended to the filename if one is not already present. The `archiveddate` frontmatter field is set at archive time.

The underscore prefix on `_Archive/` places it in the system-directory namespace (alongside `_Config`, `_Temporal`), so `is_archived_path()` is a simple string check: `"/_Archive/" in path`. Archive directories are skipped during vault walks for search, index building, and wikilink update operations — frozen snapshots whose internal links are not updated on future renames.

Archiving requires the artefact's current status to be terminal. This is enforced at the code level: `archive_artefact()` raises `ValueError` if the type has no terminal statuses or if the current status is not in that list. The `brain_edit` tool also rejects edits to archived paths, requiring `brain_action('unarchive')` first.

`+Status/` folders are stripped from the path when computing the archive destination — archived files don't need status subfolders.

## Consequences

- A path-string check is sufficient to determine archival status anywhere in the codebase.
- Archived artefacts are visible in Obsidian's file explorer under `_Archive/` but absent from `brain_list` and `brain_search` results.
- The date prefix in filenames provides a natural sort order in the archive.
- Unarchiving moves the file back to its original type folder (without the date prefix; prefix is left in place as it is harmless and aids historical tracking).
