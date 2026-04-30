# Markdown Bootstrap

Explicit degraded fallback for environments without MCP or a generated
`.brain/local/session.md` mirror.

1. Read `_Config/router.md` for vault configuration and routing rules
2. Read `_Config/User/preferences-always.md` for the vault owner's standing instructions
3. Read `_Config/User/gotchas.md` for learned lessons and known pitfalls

## Tooling

- `.brain-core/scripts/` — CLI tools for vault operations (compile_router, compile_colours, build_index, search_index, read, create, edit, rename, check, repair, fix_links, workspace_registry, vault_registry, session, shape_printable, shape_presentation, start_shaping, init, upgrade)
- Navigate the vault via wikilinks from the router and index
