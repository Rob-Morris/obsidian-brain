# Obsidian Brain

Self-extending system for organising Obsidian vaults, for agents and humans working together.

## Git Conventions

Before committing always update documentation, including `docs/changelog.md`.

## Versioning

Follows [semver](https://semver.org/). Bump the version in `VERSION` when committing user-facing changes. This is the single source of truth. The full version determines the `.brain-core/vX.Y.Z/` folder path used in vaults — update the folder name, all wikilink references, and the README when bumping.

- **Patch** — bug fixes, doc clarifications, additive changes (new artefact types, new palette colours)
- **Minor** — breaking changes to vault structure (renamed/removed core files, changed folder conventions)
- **Major** — fundamental model changes (artefact model, router contract, agent entry flow)

## Local Overrides

If `agents.local.md` exists in the repo root, read it for machine-specific configuration such as workspace paths and environment details.
