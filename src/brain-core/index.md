# Brain Core

`.brain-core/` is read-only. Never edit files here directly — changes will be overwritten on version upgrade.

## Key Idea

All content in the vault is an **artefact**:
1. **Living** (in vault root) — evolve over time, source of truth
2. **Temporal** (in _Temporal/) — bound to a moment, historic record

System folders start with `_` or `.` — these are infrastructure, not artefacts. The top-level `_Archive/` directory holds soft-deleted artefacts, excluded from the vault's active namespace.

The system is self-extending. When content has no appropriate home, add a new artefact type following documented procedures rather than forcing it into an existing folder.

## Principles

1. **Every file belongs in a folder** — no content files in the vault root
2. **Self-extending vault** — when content has no home, add a new artefact type before creating the file
3. **Always link related things** — connect artefacts with wikilinks when they relate by origin, topic, or reference
4. **Save each step before building on it** — multi-stage work produces an artefact at each stage
5. **Keep instruction files lean** — routing tables, not encyclopedias; detail lives in core docs
6. **Start simple, grow organically** — flat files first, add structure when complexity demands it
7. **Separate concerns** — one topic per artefact; split when a file serves two purposes
8. **Actively seek signal** — notice gaps, ambiguities, and opportunities; ask small questions at natural moments; capture answers as artefacts

Always:
- Use `brain_list` (`list_artefacts.py`) not `brain_search` (`search_index.py`) when enumerating or filtering artefacts by type, date range, or tag — `brain_list` is exhaustive; `brain_search` is relevance-ranked and suited for content queries only.

Always read [[.brain-core/session-polyfill]].

Without MCP, also read [[.brain-core/md-bootstrap]].
