# Brain Bootstrap

Use this file as the bootstrap entry point only. `session.start` owns the canonical bootstrap; `.brain/local/session.md` is its generated Markdown mirror, not an independently authored source of truth.

1. If MCP is available, call `session_start`.
2. Otherwise, use the launcher alternative: `brain session start --json` from the vault or a bound workspace.
3. Without the CLI, supported direct scripts remain a tool-backed route. From the vault, `python3 .brain-core/scripts/command.py session start --request-json '{}' --json` invokes the same owner when that interpreter meets its managed-runtime requirements; it does not provision them.
4. If MCP, CLI and scripts cannot be used, read `.brain/local/session.md` if it exists.
5. If no generated mirror is available either, read `.brain-core/md-bootstrap.md` for the authored Markdown fallback. It requires no code execution, compilation or generated assets.

For every tool-backed `session.start`, follow `range.next_cursor` until `bootstrap_complete` is true before ordinary work. On a source revision conflict, restart without a cursor.

`md-bootstrap.md` routes to shipped instructions and authored vault configuration. Keep this file a bootloader, not a second bootstrap payload surface.
