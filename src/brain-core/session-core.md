# Session Core

`.brain-core/` is read-only. Never edit files here directly — changes will be overwritten on version upgrade.

## Key Idea

All content in the vault is an **artefact**:

1. **Living** (in vault root) — evolve over time, source of truth
2. **Temporal** (in `_Temporal/`) — bound to a moment, historic record

System folders start with `_` or `.` — these are infrastructure, not artefacts. The top-level `_Archive/` directory holds soft-deleted artefacts, excluded from the vault's active namespace.

The system is self-extending. When content has no appropriate home, add a new artefact type following documented procedures rather than forcing it into an existing folder.

## Principles

1. **Every file belongs in a folder** — no content files in the vault root
2. **Self-extending vault** — when content has no home, add a new artefact type before creating the file
3. **Always link related things** — connect artefacts with wikilinks when they relate by origin, topic, or reference
4. **Save each step before building on it** — multi-stage work produces an artefact at each stage
5. **Keep instruction files lean** — routing tables, not encyclopaedias; detail lives in core docs
6. **Let structure express ownership** — use canonical parent relationships for containment and links or tags for association
7. **Separate concerns** — one topic per artefact; split when a file serves two purposes
8. **Actively seek signal** — notice gaps, ambiguities, and opportunities; ask small questions at natural moments; capture answers as artefacts

## Completing Bootstrap

Finish `session.start(cursor=range.next_cursor)` pages until
`bootstrap_complete` before ordinary work. For document reads, repeat the
same command and reference with `range.next_cursor` until null. On revision
conflict, restart without a cursor.

## Core Docs

- [Add types, memories and principles](standards/extending/README.md)
- [Artefact library and installation](artefact-library/README.md)
- [Workflow triggers](triggers.md)
- [Folder colours](colours.md)
- [Plugins for external tools](plugins.md)

## Standards

- [Naming conventions](standards/naming-conventions.md)
- [Canonical keys](standards/keys.md)
- [Wikilinks to existing artefacts](standards/wikilinks.md)
- [Link maintenance](standards/linking.md)
- [Provenance and lineage](standards/provenance.md)
- [Archiving](standards/archiving.md)
- [Hub pattern](standards/hub-pattern.md)
- [Subfolders](standards/subfolders.md)
- [Shaping](standards/shaping.md)
- [Preferences and gotchas](standards/user-preferences.md)

Always:
- Use `artefact.list` rather than `artefact.search` when enumerating or filtering artefacts by type, date range, or tag — list is exhaustive; search is relevance-ranked and suited to content queries.
