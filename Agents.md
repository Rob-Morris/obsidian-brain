# Obsidian Brain

A self-evolving knowledge base, for agents and humans working together on what matters. 

ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists.

## Git Discipline

- Never edit past changelog entries. Only add new entries at the top.
- Never force-add gitignored files. They are ignored for a reason.

## Agent Contributors

ALWAYS READ BEFORE contributing: `docs/contributing-agents.md`

## Agent Installs

When installing a vault on behalf of a user:

- Prefer `bash install.sh --force --skip-mcp <path>` in restricted or sandboxed environments.
- Use plain `--force` only when package index access is expected to work.
- Treat vault scaffolding and MCP setup as separate outcomes; report both.
- If MCP setup fails, keep the scaffolded vault and surface the retry commands printed by the installer.

## Before Committing

1. Run `make test` (uses `.venv` with Python 3.12; run `make install` first if the venv doesn't exist)
2. Follow `.canaries/pre-commit.md`

## Local Overrides

If `agents.local.md` exists in the repo root, read it for machine-specific configuration.
