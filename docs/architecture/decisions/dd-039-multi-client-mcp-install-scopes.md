# DD-039: Multi-client MCP install keeps native client scopes

**Status:** Accepted

## Context

Brain's current install and `init.py` flow is Claude-oriented. Project scope is documented and implemented via `.mcp.json`, and the installer assumes that is the project-level MCP config surface.

That no longer holds across clients:

- Claude supports project-, local-, and user-scoped MCP configuration with its own config surfaces.
- Codex supports user config in `~/.codex/config.toml` and project config in `.codex/config.toml`.
- Codex does not provide a distinct native local MCP scope analogous to Claude's local scope.

An earlier option was to emulate a Codex-only local scope by writing a private project `.codex/config.toml` and trying to keep it untracked. That approach is brittle because `.codex/config.toml` may legitimately contain other shared project configuration, so Brain must not redefine its meaning.

At the same time, install and init behavior must stay predictable across clients. Guessing intent from the currently running agent is not sufficient, because users may want both clients configured in the same project.

## Decision

Brain keeps each client's native scope model rather than emulating missing scopes.

- `init.py` becomes client-aware via an explicit `--client claude|codex|all` interface.
- `--client all` means "write every supported client config for the requested scope".
- Claude keeps native `project`, `local`, and `user` support.
- Codex supports native `project` and `user` support only.
- `--client codex --local` is unsupported and must warn or fail without writing configuration.
- Brain does not invent a synthetic Codex local scope.

Install and removal semantics follow from that scope model.

- `install.sh` defaults to project-only setup when MCP setup is enabled.
- User/global setup is explicit rather than a side effect of normal install.
- `--client all --local` applies Claude local setup, skips Codex local setup, prints a clear warning, and exits success.
- `init.py` records what it wrote in machine-local state under `.brain/local/`.
- `install.sh --uninstall` removes recorded Brain-managed project entries when uninstalling that vault/project.
- User-scope cleanup is explicit only and lives in `init.py --remove` rather than normal uninstall.
- `--force` skips confirmation for the scoped removal already requested, but does not broaden cleanup to user/global config.

## Consequences

- The configuration model matches official client behavior instead of papering over differences.
- Users can intentionally configure both Claude and Codex in the same project by using `--client all`.
- The installer configures supported clients for the target project by default without depending on the active shell environment.
- Codex local scope is explicit as unsupported, which is clearer than a private-file workaround that could collide with real tracked config.
- Project cleanup is automatic when Brain can prove ownership, while user/global cleanup stays explicit and scope-aware.
- `init.py` becomes the owner of MCP registration lifecycle: add, update, verify, and explicit remove.
