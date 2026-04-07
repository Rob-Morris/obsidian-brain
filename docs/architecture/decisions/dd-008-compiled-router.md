# DD-008: Compiled Router as Foundation

**Status:** Implemented (v0.5.0)

## Context

Tools and scripts need to know the vault's artefact types, naming patterns, required frontmatter fields, triggers, and status enums. Reading this from scattered markdown files on every operation is slow and fragile. A single configuration source that tooling can depend on was needed.

## Decision

Introduce a compiled router: `router.md`, taxonomy files, skills, styles, and `VERSION` are the human-authored source of truth. The compiler (`compile_router.py`) combines them into `.brain/local/compiled-router.json` — a local, gitignored, hash-invalidated cache. All tools require the compiled router; they do not read source markdown directly.

Key properties:
- **Filesystem-first discovery** (see DD-016) — artefact types are discovered by scanning vault folders.
- **Hash invalidation** — SHA-256 of every source file stored in `meta.sources`. Stale the moment any source changes.
- **Environment-specific** — includes platform, runtime state, absolute vault root. Never committed to git.
- **Auto-compile** — MCP server compiles on startup if missing or stale (DD-014); all tools auto-compile if needed (DD-013).

## Consequences

- All tooling reads from a single, structured JSON contract rather than parsing markdown.
- Adding a new artefact type only requires adding a taxonomy file and recompiling — no tool changes needed.
- The compiled router is local-only; shared vaults must each compile their own.
- On mobile (no Python), agents fall back to the lean router and wikilink traversal (DD-006, DD-012).
