# Migration to v0.63.0

Removes the legacy `yyyymmdd - ` prefix from files whose frontmatter declares `type: living/note`. The migration scans `Notes/` recursively, preserves each note's current folder, and updates matching vault wikilinks through the shared rename machinery.

The migration preflights the complete move set before changing files. An existing destination or a resulting ambiguous Note title stops the upgrade so the collision can be resolved explicitly. Title checks ignore case and include documents in other folders, protecting case-insensitive filesystems and basename wikilinks. Give colliding documents distinct titles before retrying.

The migration declares all files in the shared wikilink rewrite surface, including root bootstrap documents and unconfigured folders, for upgrade snapshots. The upgrader restores these snapshots on failure.

After upgrading, verify that living Notes use human-readable `{Title}.md` filenames and run `brain links check` if you want an additional link-integrity check.
