# Obsidian Brain

A self-evolving knowledge base, for agents and humans working together on what matters. 

ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists.

## Git Discipline

- Never edit past changelog entries. Only add new entries at the top.
- Never force-add gitignored files. They are ignored for a reason.

## Agent Contributors

ALWAYS READ BEFORE contributing: `docs/contributing-agents.md`

## Before Committing

1. Run `make test` (uses `.venv` with Python 3.12; run `make install` first if the venv doesn't exist)
2. Follow `.canaries/pre-commit.md`

## Local Overrides

If `agents.local.md` exists in the repo root, read it for machine-specific configuration.
