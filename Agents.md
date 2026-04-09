# Obsidian Brain

Self-extending system for organising Obsidian vaults, for agents and humans working together.

## Git Discipline

- Never edit past changelog entries. Only add new entries at the top.
- Never force-add gitignored files. They are ignored for a reason.

## Before Committing

1. Run `make test` (uses `.venv` with Python 3.12; run `make install` first if the venv doesn't exist)
2. Follow `.canaries/pre-commit.md`

## Local Overrides

If `agents.local.md` exists in the repo root, read it for machine-specific configuration.

ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]
