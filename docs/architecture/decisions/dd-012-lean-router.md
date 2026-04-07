# DD-012: Lean Router — Always-Rules Only, Conditional Triggers Co-Located

**Status:** Accepted

## Context

The router file is read by agents on every session, including those on mobile with no compiled router. If it duplicates the full content of every trigger's instructions, it becomes expensive to load and hard to maintain in sync with the taxonomy files it references.

## Decision

The lean router (`router.md`) contains only always-rules — constraints that apply unconditionally. Conditional triggers use a goto pattern: the router states WHEN (one-line condition + wikilink pointer); the target taxonomy or skill file states WHAT and HOW in its `## Trigger` section. Zero duplication — the taxonomy file is the single source of truth for trigger instructions.

Always-rules live in two places: system-level rules in `index.md` (version-bound) and vault-specific additions in `router.md` (optional). The compiler merges both into `always_rules` in the compiled router.

## Consequences

- The router file stays small and cheap to load — critical for mobile agents reading markdown directly.
- Trigger logic is maintained in one place (the taxonomy file), not synchronised between router and taxonomy.
- Agents must follow wikilinks to read trigger instructions — one extra hop vs. inline content.
- The goto pattern makes the router a navigable index rather than a self-contained specification.
