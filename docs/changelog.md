# Changelog

## v1.0 — 2026-03-14

Core/config split. Separated Brain methodology into versioned `.brain-core/` and restructured `_Config/` for instance-specific configuration.

### Added
- `src/brain-core/` — core methodology source (index, artefacts, extensions, triggers, colours, plugins, naming)
- `vault/.brain-core/v1.0/` — symlink to `src/brain-core/`
- `vault/router.md` — single per-session file replacing the instruction chain
- `vault/_Config/style.md`, `principles.md`, `colours.md` — instance config at `_Config/` root
- `vault/Wiki/` — starter living artefact folder
- Living vs temporal artefact model with example library in `extensions.md`

### Changed
- `vault/CLAUDE.md` — slimmed to entry pointer (`→ router.md`)
- `vault/_Config/Skills/vault-maintenance/` — updated for router-based workflow
- `vault/_Config/Skills/vault-maintenance/scripts/compliance_check.py` — updated ROOT_ALLOW, removed Daily Note check, added expected folders check
- `vault/.obsidian/snippets/folder-colours.css` — removed non-shipped folder styles, added Wiki (teal) and router.md styling
- Templates generalised (removed hardcoded timestamps, removed SOURCE_TYPE tag)

### Removed
- `vault/_Config/Instructions/` — entire folder (README, Structure, Taxonomy, Principles, Workflow Triggers, Writing Style)
- `vault/_Config/Assets/Folder Colours.md` — colour system design moved to core
- `vault/_Config/Templates/Daily Note Template.md`
- `vault/.obsidian/daily-notes.json`
- Artefact folders: Daily Notes, Designs, Documentation, Ideas, Notes, Projects
- Temporal folders: Plans, Research

## Initial release — 2026-03-14

- Three-tier folder structure: artefact, temporal, config
- Plugin folder (`_Plugins/`) with extension procedure for adding tool integrations
- Vault-maintenance skill with compliance check script and evals
- Folder colour CSS with palette system
- Templates for daily notes, logs, and transcripts
- Obsidian config with Front Matter Timestamps and Minimal Theme Settings plugins
