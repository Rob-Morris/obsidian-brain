# Obsidian Brain

A self-evolving knowledge base, for agents and humans working together on what matters. 

ALWAYS DO FIRST: Call MCP `brain_session`.

## Agent Contributors

ALWAYS READ BEFORE contributing: `docs/contributor/agents.md`

## Documentation

- `docs/README.md` — documentation root index
- `docs/user/README.md` — user setup, workflows, and reference
- `docs/functional/README.md` — MCP tools, scripts, CLI, and configuration contracts
- `docs/architecture/README.md` — system structure, security, boundaries, and decisions
- `docs/contributor/README.md` — contribution workflow and contributor-facing product docs
- `docs/CHANGELOG.md` — release history

## Before Committing

1. Run `make test` (uses `.venv` with Python 3.12; run `make install` first if the venv doesn't exist)
2. Follow `.canaries/pre-commit.md`
3. Never force-add gitignored files. They are ignored for a reason.

## Local Overrides

If `AGENTS.local.md` exists in the repo root, read it for machine-specific configuration.
