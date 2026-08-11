# Markdown Bootstrap

Explicit degraded fallback for environments without MCP or a generated
`.brain/local/session.md` mirror.

1. Read `_Config/router.md` for vault configuration and routing rules
2. Read `_Config/User/preferences-always.md` for the vault owner's standing instructions
3. Read `_Config/User/gotchas.md` for learned lessons and known pitfalls

## Tooling

- `.brain-core/scripts/command.py command list --request-json '{}' --json` — discover the installed selected-Brain commands
- `.brain-core/scripts/command.py <noun> <verb> --request-json '<object>' --json` — invoke one selected-Brain command through the canonical direct projection
- `brain command list --owner all --json` — discover the composed local CLI catalogue when CLI 2 is available
- Navigate the vault via wikilinks from the router and index
