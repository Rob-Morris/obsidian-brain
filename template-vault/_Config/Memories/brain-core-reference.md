---
triggers: [brain core, obsidian-brain, brain system, vault system]
---

# Brain Core Reference

Brain-core is the system that organises this Obsidian vault. It provides structure, conventions, and tooling for agents and humans working together.

## Key Locations

- **Source repo:** `obsidian-brain` (GitHub) — the development repo
- **Installed copy:** `.brain-core/` in the vault root — versioned, upgradeable
- **Version file:** `.brain-core/VERSION`
- **Config:** `_Config/` — router, taxonomy, styles, templates, skills, memories, user preferences

## How It Works

Every file belongs in a typed folder. Types are either **living** (evolve over time, root-level folders) or **temporal** (point-in-time snapshots, under `_Temporal/`). The router (`_Config/router.md`) is the agent entry point. The compiler (`compile_router.py`) builds a JSON cache from all config files.

## Key Docs

- `.brain-core/index.md` — bootstrap entry point for MCP, generated markdown, and degraded fallback paths
- `.brain-core/session-core.md` — static authored source for core bootstrap content
- `.brain-core/guide.md` — quick-start guide
- `.brain-core/standards/extending/README.md` — how to add types, memories, and other extensions
- `_Config/Taxonomy/` — one file per artefact type with full definition

## Extensions

New artefact types, skills, styles, and memories can be added without modifying brain-core itself. Run `python3 .brain-core/scripts/compile_router.py` after changes to regenerate the compiled router.
