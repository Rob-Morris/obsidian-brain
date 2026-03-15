# Obsidian Brain

Self-extending system for organising Obsidian vaults, for agents and humans working together.

## Git Conventions

Before committing always update documentation, including `docs/changelog.md`.

## Versioning

Follows [semver](https://semver.org/). The version is tracked in `.brain-core/VERSION` (inside `src/brain-core/VERSION` in this repo). This is the single source of truth. The `.brain-core/` path is unversioned and stable across upgrades — no folder renames or wikilink rewrites needed when bumping.

- **Patch** — bug fixes, doc clarifications, additive changes (new artefact types, new palette colours)
- **Minor** — breaking changes to vault structure (renamed/removed core files, changed folder conventions)
- **Major** — fundamental model changes (artefact model, router contract, agent entry flow)

## Multi-Repo Workflow

Core-first workflow: when changes span brain-core and vault repos, implement and commit core changes first, then propagate to vault repos. Never deploy to both simultaneously.

## Local Overrides

If `agents.local.md` exists in the repo root, read it for machine-specific configuration such as workspace paths and environment details.
