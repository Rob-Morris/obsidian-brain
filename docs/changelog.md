# Changelog

Follows [semver](https://semver.org/). Changes to vault structure (renamed/removed core files, changed folder conventions) are breaking and bump the minor version.

## v0.4.0 — 2026-03-15

- Lean router format (DD-017) — artefact tables removed, conditional triggers as goto pointers to taxonomy/skill files
- `taxonomy.md` → `taxonomy/readme.md` — lean discovery guide replaces full artefact reference (DD-018, DD-019)
- Trigger sections added to taxonomy files — each type now has a `## Trigger` section with the full condition and action (DD-017)
- `Agents.md` simplified to single-line install (DD-015) — user directives only, no vault instructions
- Added `docs/tooling.md` — technical design reference with DD-001 through DD-019 index

## v0.3.0 — 2026-03-15

**Breaking:** Dropped version from `.brain-core/` path. Vaults referencing `.brain-core/v0.2.1/` must rewrite wikilinks to `.brain-core/`. This is the last path-related breaking change — wikilinks are now stable across upgrades.

- Moved version tracking from folder path to `.brain-core/VERSION` file
- Removed root `VERSION` file (version now lives inside brain-core itself)
- Rewrote all wikilinks and prose references to use unversioned `.brain-core/` path
- Template vault `.brain-core/` is now a direct symlink (was `.brain-core/v0.2.1/` → `../../src/brain-core`)

## v0.2.1 — 2026-03-15

- Added `Agents.md` with git conventions, versioning, and local overrides; `CLAUDE.md` symlink
- Added `VERSION` file as single source of truth for semver
- Added changelog maintenance to git conventions
- Rebased version numbering to start at v0 (pre-1.0)

## v0.2.0 — 2026-03-15

**Breaking:** Renamed core files — vaults referencing `.brain-core/v0.1.x/artefacts`, `.brain-core/v0.1.x/naming`, or `.brain-core/v0.1.x/principles` must update wikilinks. Folder path changes to `.brain-core/v0.2.0/`.

- Consolidated core docs: merged `artefacts.md` + `naming.md` into `taxonomy.md`, inlined `principles.md` into `index.md`
- Folder colours: _Temporal rose, _Plugins orchid, all system folders double border
- System folder icons (⍟ ⬡ ◷) floated right via CSS `::after` pseudo-elements, plus ⍟ on Agents.md/CLAUDE.md
- Log taxonomy: added cross-repo tagging convention and summary artefact relationship

## v0.1.1 — 2026-03-15

- Fixed 12 inconsistencies across core, template-vault, and specification
- Added example library of artefact type definitions
- Qualified artefact statement in specification and README
- Added Plans as a temporal artefact type
- Documented `.brain-core` as copy, not symlink

## v0.1.0 — 2026-03-14

Initial release.

- Core methodology in `src/brain-core/` (artefacts, extensions, triggers, colours, plugins, naming)
- Template vault with `CLAUDE.md` → `router.md` agent entry flow
- Living vs temporal artefact model with example library
- Starter artefacts: Wiki, Logs, Transcripts
- Instance config at `_Config/` root: style, principles
- Vault-maintenance skill with compliance check script and evals
- Folder colour CSS with 16-colour pastel palette
- Plugin system with gold-styled `_Plugins/` folder
- Obsidian config with Front Matter Timestamps and Minimal Theme Settings
