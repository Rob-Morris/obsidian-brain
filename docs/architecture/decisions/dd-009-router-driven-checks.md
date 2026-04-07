# DD-009: Router-Driven Checks — No Separate Check Config

**Status:** Implemented (v0.9.11)

## Context

A vault compliance checker needs to know, for each artefact type: the naming pattern, required frontmatter fields, valid status values, and terminal statuses. This information could live in a dedicated check config, or it could be extracted from the same taxonomy files that drive the router.

## Decision

`check.py` reads the compiled router and derives all per-type rules from it. It never parses taxonomy markdown directly. The compiler is the single point that must track vault evolution; `check.py` adapts automatically when the router is recompiled.

The check catalogue covers: root file placement, naming patterns, frontmatter type field, required fields, month folder placement, archive metadata, status value validity, broken wikilinks, and ambiguous wikilinks.

## Consequences

- No separate check config to maintain or synchronise with taxonomy.
- Adding a new artefact type or changing a naming convention automatically propagates to checks on next compile.
- The compiler must correctly extract `status_enum` and `terminal_statuses` from taxonomy markdown — these fields power `check.py`'s status and archive checks.
- `check.py` is stateless, idempotent, and stdlib-only — safe to run on demand or in CI.
