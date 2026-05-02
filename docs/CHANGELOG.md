# Changelog

Follows a pre-1.0 [semver](https://semver.org/) policy: backward-compatible changes are patch; breaking Brain changes are minor; only fundamental model changes are major. Breaking Brain changes include vault-structure changes and breaking tool/script/MCP contract changes. Fundamental model changes are changes to the artefact model, router contract, or agent bootstrap/entry flow.

Version-by-version release history for Obsidian Brain. Every shipped version has its own file under `docs/changelog/`; milestone releases additionally have richer release files under `docs/changelog/releases/`.

| Version | Date | Summary |
|---|---|---|
| [v0.35.0](changelog/v0.35.0.md) | 2026-05-02 | Split MCP startup with `brain_init` and background warmup |
| [v0.34.2](changelog/v0.34.2.md) | 2026-05-01 | Prepare release/changelog docs via scope terminology |
| [v0.34.1](changelog/v0.34.1.md) | 2026-05-01 | Harden definition sync against false conflicts from stale tracking |
| [v0.34.0](changelog/v0.34.0.md) | 2026-04-30 | Realign release artefacts as milestone-first with required parent-agnostic ownership |
| [v0.33.0](changelog/v0.33.0.md) | 2026-04-30 | Reshape MCP surface, park brain-process work, and tighten contracts |
| [v0.32.10](changelog/v0.32.10.md) | 2026-04-28 | Add a `code-review` core skill family for triaged reviews and guided fixups. |
| [v0.32.9](changelog/v0.32.9.md) | 2026-04-27 | Restrict repair.py mcp to installed clients and preserve user CLAUDE.md on uninstall |
| [v0.32.8](changelog/v0.32.8.md) | 2026-04-27 | Move MCP proxy recovery/backoff onto a dedicated thread |
| [v0.32.7](changelog/v0.32.7.md) | 2026-04-27 | Consolidate MCP proxy child-loss recovery into one restart coordinator |
| [v0.32.6](changelog/v0.32.6.md) | 2026-04-26 | Add repair.py infrastructure recovery entry point |
| [v0.32.5](changelog/v0.32.5.md) | 2026-04-26 | Add shortest-clearest-unique slug derivation and apply it in v0.31.0 migration |
| [v0.32.4](changelog/v0.32.4.md) | 2026-04-26 | Collapse brain_edit artefact mutation entry points |
| [v0.32.3](changelog/v0.32.3.md) | 2026-04-26 | Retire legacy find_section helpers |
| [v0.32.2](changelog/v0.32.2.md) | 2026-04-26 | Simplify brain_edit after v0.32.0 rollout |
| [v0.32.1](changelog/v0.32.1.md) | 2026-04-25 | Fix brain_edit MCP consistency for memories |
| [v0.32.0](changelog/v0.32.0.md) | 2026-04-25 | Roll out brain_edit structural contract |
| [v0.31.3](changelog/v0.31.3.md) | 2026-04-26 | Canonical upgrade ownership and Python 3.12 tooling contract |
| [v0.31.2](changelog/v0.31.2.md) | 2026-04-25 | Workspace +Completed consistency |
| [v0.31.1](changelog/v0.31.1.md) | 2026-04-25 | MCP router staleness short-circuit |
| [v0.31.0](changelog/v0.31.0.md) | 2026-04-24 | Brain Hub Key Convention — canonical living-artefact keys |
| [v0.30.2](changelog/v0.30.2.md) | 2026-04-21 | Tolerate historical bootstrap casing without migrating local overrides |
| [v0.30.1](changelog/v0.30.1.md) | 2026-04-21 | Restructure repo docs into four layers; adopt Agent-Ready Documentation Standard v1.0 |
| [v0.30.0](changelog/v0.30.0.md) | 2026-04-19 | **Release: [Vault Tooling Maturity](changelog/releases/v0.30.0-vault-tooling-maturity.md)** |
| [v0.29.7](changelog/v0.29.7.md) | 2026-04-19 | Align zettelkasten definitions; rename parent→follows |
| [v0.29.6](changelog/v0.29.6.md) | 2026-04-19 | Harden embeddings writes via safe_write_via kernel |
| [v0.29.5](changelog/v0.29.5.md) | 2026-04-19 | Phase 1 mutation-safety hardening |
| [v0.29.4](changelog/v0.29.4.md) | 2026-04-18 | Key temporal logs off subject day; fail closed on rename collisions |
| [v0.29.3](changelog/v0.29.3.md) | 2026-04-18 | Make pre-compile rollback snapshots binary-safe. |
| [v0.29.2](changelog/v0.29.2.md) | 2026-04-18 | Add pre_compile_patch migration stage; harden upgrade runner |
| [v0.29.1](changelog/v0.29.1.md) | 2026-04-18 | Harden concurrent mutation handling in the MCP server and shared write path. |
| [v0.29.0](changelog/v0.29.0.md) | 2026-04-18 | Frontmatter-backed filename rendering |
| [v0.28.8](changelog/v0.28.8.md) | 2026-04-18 | Warn on broken wikilinks at create/edit; add single-file fix-links |
| [v0.28.7](changelog/v0.28.7.md) | 2026-04-17 | Restore sync_definitions install path, add status mode |
| [v0.28.6](changelog/v0.28.6.md) | 2026-04-17 | Add release artefact type and status-aware naming |
| [v0.28.5](changelog/v0.28.5.md) | 2026-04-16 | Fix GitHub URL and install path examples |
| [v0.28.4](changelog/v0.28.4.md) | 2026-04-16 | Finish the short-term local runtime contract for `printables`. |
| [v0.28.3](changelog/v0.28.3.md) | 2026-04-16 | Bring generated-asset workflows into line across printables, presentations, and mockups. |
| [v0.28.2](changelog/v0.28.2.md) | 2026-04-16 | Add a `printables` artefact type for page-based PDF exports. |
| [v0.28.1](changelog/v0.28.1.md) | 2026-04-16 | Harden v0.28.0 vault registry |
| [v0.28.0](changelog/v0.28.0.md) | 2026-04-16 | Cross-vault registry at `~/.config/brain/vaults`. |
| [v0.27.10](changelog/v0.27.10.md) | 2026-04-15 | Style the root `_Archive/` folder. |
| [v0.27.9](changelog/v0.27.9.md) | 2026-04-14 | Move workspace manifest to `.brain/local/workspace.yaml` — all fields are install-specific. |
| [v0.27.8](changelog/v0.27.8.md) | 2026-04-14 | Make project-scope MCP activation explicit instead of pretending registration is sufficient. |
| [v0.27.7](changelog/v0.27.7.md) | 2026-04-14 | Drop the last `mcp/` transport shims now that legacy installs are repaired during upgrade. |
| [v0.27.6](changelog/v0.27.6.md) | 2026-04-14 | Repair legacy MCP registrations that still launch the pre-`brain_mcp` transport paths. |
| [v0.27.5](changelog/v0.27.5.md) | 2026-04-13 | Make bootstrap workspace-aware and scaffold workspace-owned metadata during folder setup. |
| [v0.27.4](changelog/v0.27.4.md) | 2026-04-13 | Rename the leading body-range target to `:body_preamble` and stop it before the first targetable section. |
| [v0.27.3](changelog/v0.27.3.md) | 2026-04-13 | Tighten follow-up fixes for shutdown logging and legacy `brain_edit` target rejection. |
| [v0.27.2](changelog/v0.27.2.md) | 2026-04-13 | Tolerate closed stdio pipes during MCP server shutdown. |
| [v0.27.1](changelog/v0.27.1.md) | 2026-04-13 | Make whole-body `brain_edit` targeting explicit and reject legacy `:body`. |
| [v0.27.0](changelog/v0.27.0.md) | 2026-04-13 | Support native multi-client MCP install for Claude Code and Codex, with a tightened installer contract. |
| [v0.26.5](changelog/v0.26.5.md) | 2026-04-13 | Fix `brain_action("convert")` producing filenames that nest the source type's naming prefix inside the target pattern. |
| [v0.26.4](changelog/v0.26.4.md) | 2026-04-13 | Tidy the migration-ledger runner without changing its semantics. |
| [v0.26.3](changelog/v0.26.3.md) | 2026-04-13 | Persist migration history in `.brain/local` so reinstalling `.brain-core/` does not replay old migrations. |
| [v0.26.2](changelog/v0.26.2.md) | 2026-04-12 | Make installer MCP setup optional and non-fatal for agent-driven installs. |
| [v0.26.1](changelog/v0.26.1.md) | 2026-04-12 | Harden fresh installs against leaked machine-local template state. |
| [v0.26.0](changelog/v0.26.0.md) | 2026-04-12 | Package the MCP transport as `brain_mcp/` and leave compatibility shims in `mcp/`. |
| [v0.25.1](changelog/v0.25.1.md) | 2026-04-12 | Remove contributor-only workflow tiers from shipped bootstrap. |
| [v0.25.0](changelog/v0.25.0.md) | 2026-04-12 | **Release: [Architecture & Session Unification](changelog/releases/v0.25.0-architecture-and-session-unification.md)** |
| [v0.24.12](changelog/v0.24.12.md) | 2026-04-12 | Extract MCP tool handlers behind sibling modules. |
| [v0.24.11](changelog/v0.24.11.md) | 2026-04-12 | Formalise the tiered workflow in agent instructions. |
| [v0.24.10](changelog/v0.24.10.md) | 2026-04-12 | Add a thin Gherkin layer for core domain behaviours. |
| [v0.24.9](changelog/v0.24.9.md) | 2026-04-11 | Promote router and artefact helpers into the `_common` shared kernel. |
| [v0.24.8](changelog/v0.24.8.md) | 2026-04-11 | Explicit whole-section replacement mode for `brain_edit`. |
| [v0.24.7](changelog/v0.24.7.md) | 2026-04-11 | Decompose `_common.py` into `_common/` package. |
| [v0.24.6](changelog/v0.24.6.md) | 2026-04-10 | **Release: [Operational Maturity](changelog/releases/v0.24.6-operational-maturity.md)** |
| [v0.24.5](changelog/v0.24.5.md) | 2026-04-10 | MCP proxy writer thread cleanup. |
| [v0.24.4](changelog/v0.24.4.md) | 2026-04-10 | MCP proxy: single-writer queue, universal drift decoration, eager drift detection. |
| [v0.24.3](changelog/v0.24.3.md) | 2026-04-10 | MCP proxy reliability: replay, drift detection, health check. |
| [v0.24.2](changelog/v0.24.2.md) | 2026-04-10 | Definition sync auto-applies safe updates. |
| [v0.24.1](changelog/v0.24.1.md) | 2026-04-09 | MCP proxy/server reliability fixes. |
| [v0.24.0](changelog/v0.24.0.md) | 2026-04-09 | Bootstrap streamlining. |
| [v0.23.6](changelog/v0.23.6.md) | 2026-04-09 | Safe temp path generation. |
| [v0.23.5](changelog/v0.23.5.md) | 2026-04-09 | Shaping skill redesign and swarm-test multi-skill architecture. |
| [v0.23.4](changelog/v0.23.4.md) | 2026-04-08 | Promote bug-logs, mockups, presentations to template vault defaults. |
| [v0.23.3](changelog/v0.23.3.md) | 2026-04-08 | Fix version-drift exit code for proxy restart. |
| [v0.23.2](changelog/v0.23.2.md) | 2026-04-08 | Wiki topic cluster guidance. |
| [v0.23.1](changelog/v0.23.1.md) | 2026-04-07 | scripts/README.md added. |
| [v0.23.0](changelog/v0.23.0.md) | 2026-04-07 | Add MCP proxy wrapper for upgrade-safe server restarts |
| [v0.22.8](changelog/v0.22.8.md) | 2026-04-07 | Operator key generator. |
| [v0.22.7](changelog/v0.22.7.md) | 2026-04-07 | Brain_edit gains resource param for editing skills, memories, styles, templates |
| [v0.22.6](changelog/v0.22.6.md) | 2026-04-07 | Auto-move resolves to sibling +Status/ folder when already in +Status/ |
| [v0.22.5](changelog/v0.22.5.md) | 2026-04-06 | Version drift raises RuntimeError before exit for graceful MCP error |
| [v0.22.4](changelog/v0.22.4.md) | 2026-04-06 | Brain_create gains resource param for creating skills, memories, styles, templates |
| [v0.22.3](changelog/v0.22.3.md) | 2026-04-06 | Add superseded terminal status to designs taxonomy |
| [v0.22.2](changelog/v0.22.2.md) | 2026-04-06 | Brain_search gains resource param for cross-resource searching |
| [v0.22.1](changelog/v0.22.1.md) | 2026-04-06 | Read/list split and resource-scoped listing |
| [v0.22.0](changelog/v0.22.0.md) | 2026-04-06 | Add write guards blocking dot-prefixed and protected underscore folders |
| [v0.21.9](changelog/v0.21.9.md) | 2026-04-06 | Mcp server robustness — BaseException, signal handler, exc_info, silent swallows |
| [v0.21.8](changelog/v0.21.8.md) | 2026-04-06 | Support null-means-delete for frontmatter fields |
| [v0.21.7](changelog/v0.21.7.md) | 2026-04-06 | Add MCP server logging |
| [v0.21.6](changelog/v0.21.6.md) | 2026-04-06 | Auto-set `statusdate` on status transitions. |
| [v0.21.5](changelog/v0.21.5.md) | 2026-04-06 | Sync artefact definitions after upgrade |
| [v0.21.4](changelog/v0.21.4.md) | 2026-04-06 | MCP server version-drift hang, startup timeout, upgrade rollback |
| [v0.21.3](changelog/v0.21.3.md) | 2026-04-06 | Add documentation lifecycle — new, shaping, ready, active, deprecated |
| [v0.21.2](changelog/v0.21.2.md) | 2026-04-06 | Accept /tmp as valid body_file root on macOS |
| [v0.21.1](changelog/v0.21.1.md) | 2026-04-06 | Add delete_section operation to brain_edit, fix empty-body normalization |
| [v0.21.0](changelog/v0.21.0.md) | 2026-04-06 | Archive visibility — _Archive/ invisible to normal operations |
| [v0.20.1](changelog/v0.20.1.md) | 2026-04-05 | Fix: `_Archive/` folders are now immune to auto-move side-effects. |
| [v0.20.0](changelog/v0.20.0.md) | 2026-04-05 | Feature: `brain_edit` now automatically moves artefacts to `+Status/` folders when setting a terminal status, and moves them back out when reverting to a non-terminal status. |
| [v0.19.7](changelog/v0.19.7.md) | 2026-04-05 | **Release: [Security & Stability](changelog/releases/v0.19.7-security-and-stability.md)** |
| [v0.19.6](changelog/v0.19.6.md) | 2026-04-04 | Fix: MCP tool handlers (`body_file`, rename, shape-presentation, upgrade) accepted unbounded filesystem paths, allowing reads/writes/deletes outside the vault. |
| [v0.19.5](changelog/v0.19.5.md) | 2026-04-04 | Fix: MCP server now handles graceful shutdown per the stdio lifecycle spec. |
| [v0.19.4](changelog/v0.19.4.md) | 2026-04-04 | Feat: `compile_router` now emits `frontmatter_type` on every artefact dict. |
| [v0.19.3](changelog/v0.19.3.md) | 2026-04-04 | Refactor: Retired `docs/standards/` subfolder — single-file folder wasn't earning its keep. |
| [v0.19.2](changelog/v0.19.2.md) | 2026-04-04 | Fix: Migration scripts failing during MCP upgrade due to stale module cache. |
| [v0.19.1](changelog/v0.19.1.md) | 2026-04-04 | Refactor: Unified template placeholder substitution — new `substitute_template_vars()` helper in `_common.py` handles `{{date:FORMAT}}` and custom variable replacement. |
| [v0.19.0](changelog/v0.19.0.md) | 2026-04-03 | Shaping standard — single source of truth for iterative Q&A refinement of vault artefacts. |
| [v0.18.16](changelog/v0.18.16.md) | 2026-04-04 | Document brain_list, bump to v0.18.16 |
| [v0.18.15](changelog/v0.18.15.md) | 2026-04-04 | Fix: Router staleness detection now covers config resources (skills, plugins, styles, memories), not just artefact types. |
| [v0.18.14](changelog/v0.18.14.md) | 2026-04-04 | Feature: `init.py` defaults to current directory (no flags needed) instead of vault root. |
| [v0.18.13](changelog/v0.18.13.md) | 2026-04-04 | Fix: `resolve_artefact_path` now finds temporal artefacts by display name. |
| [v0.18.12](changelog/v0.18.12.md) | 2026-04-03 | Feature: `brain_edit` gains a `prepend` operation — inserts content before a section heading (or at the start of the body). |
| [v0.18.11](changelog/v0.18.11.md) | 2026-04-03 | Fix: `brain_action("rename")` no longer silently fails on cross-directory moves when Obsidian CLI is available. |
| [v0.18.10](changelog/v0.18.10.md) | 2026-04-03 | Fix: `sync_definitions` no longer proposes installing new artefact types during upgrade. |
| [v0.18.9](changelog/v0.18.9.md) | 2026-04-03 | Fix: `convert_artefact` now preserves the hub parent subfolder when converting between living types. |
| [v0.18.8](changelog/v0.18.8.md) | 2026-04-03 | Fix: Stop `built_at` advancing on incremental index updates. |
| [v0.18.7](changelog/v0.18.7.md) | 2026-04-02 | Fix: Guard `_check_router`, `_check_index`, and `_load_embeddings` against corrupted JSON cache files (valid JSON that isn't a dict). |
| [v0.18.6](changelog/v0.18.6.md) | 2026-04-01 | Refactor: Extract `now_iso()` utility to `_common.py`; `edit.py` uses it instead of inlining `datetime.now(timezone.utc).astimezone().isoformat()`. |
| [v0.18.5](changelog/v0.18.5.md) | 2026-04-02 | Script-level timestamps (edit): `edit_artefact()` and `append_to_artefact()` now update the `modified` frontmatter field on every write. |
| [v0.18.4](changelog/v0.18.4.md) | 2026-04-02 | Refactor: `create_artefact()` now captures a single `datetime.now()` and passes it to `resolve_naming_pattern()` and `resolve_folder()`, ensuring filename, folder path, and frontmatter timestamps all reflect the same instant. |
| [v0.18.3](changelog/v0.18.3.md) | 2026-04-02 | Script-level timestamps: `create_artefact()` now injects `created` and `modified` ISO 8601 timestamps into frontmatter at write time. |
| [v0.18.2](changelog/v0.18.2.md) | 2026-03-31 | Rename `brain_read` resources for clarity. |
| [v0.18.1](changelog/v0.18.1.md) | 2026-03-31 | Fix `sync_definitions --force` installing uninstalled artefact types. |
| [v0.18.0](changelog/v0.18.0.md) | 2026-03-31 | `+Status` folders for terminal-status artefacts — artefacts reaching terminal status now move to `+Status/` folders within their type directory (e.g. |
| [v0.17.4](changelog/v0.17.4.md) | 2026-03-31 | Fix `brain_edit` body wipe on frontmatter-only edits — `edit_artefact` unconditionally replaced the body with the `body` parameter, so a frontmatter-only edit (`body=""`) would silently clear the entire document. |
| [v0.17.3](changelog/v0.17.3.md) | 2026-03-31 | Fix stale doc references — updated 6 files still pointing to removed `extensions.md` (decomposed into `standards/extending/` in v0.13.0). |
| [v0.17.2](changelog/v0.17.2.md) | 2026-03-31 | Dependency management for vault MCP server — `install.sh` now reads from `.brain-core/mcp/requirements.txt` instead of hardcoding dependencies. |
| [v0.17.1](changelog/v0.17.1.md) | 2026-03-31 | Fix `brain_read(resource="file")` for non-artefact paths — full relative paths (containing `/`) now read any vault file directly, bypassing artefact folder validation. |
| [v0.17.0](changelog/v0.17.0.md) | 2026-03-30 | Vault config system and operator profiles — new `.brain/config.yaml` with two-zone model: `vault` zone (shared authority, profiles and operators) and `defaults` zone (customisable per-machine). |
| [v0.16.10](changelog/v0.16.10.md) | 2026-03-30 | Rewrite Obsidian CLI integration from subprocess to IPC socket — connects directly to Obsidian's `~/.obsidian-cli.sock` instead of spawning the Obsidian binary as a subprocess. |
| [v0.16.9](changelog/v0.16.9.md) | 2026-03-30 | Add `living/task` artefact type — brain-native tasks as persistent, queryable units of work. |
| [v0.16.8](changelog/v0.16.8.md) | 2026-03-30 | Normalise `.md` extension in artefact and file paths — agents no longer need to include the `.md` extension when passing paths to `brain_edit`, `brain_read`, or other artefact operations. |
| [v0.16.7](changelog/v0.16.7.md) | 2026-03-30 | `body_file` parameter for `brain_create` and `brain_edit` — agents can pass large body content via a temp file path instead of inline, keeping MCP call displays compact. |
| [v0.16.6](changelog/v0.16.6.md) | 2026-03-29 | Fix section-targeted edit corrupting following headings — `brain_edit` with a `target` section would concatenate replacement content directly with the next heading when the body lacked a trailing newline, corrupting the heading and making it invisible to subsequent section-targeted operations. |
| [v0.16.5](changelog/v0.16.5.md) | 2026-03-29 | Basename resolution for `brain_read` and `brain_edit` — `brain_read(resource="file")` and `brain_edit` now accept basenames in addition to full relative paths, resolving them like wikilinks (case-insensitive, `.md`-optional). |
| [v0.16.4](changelog/v0.16.4.md) | 2026-03-29 | Broken link auto-repair — New `fix_links.py` script and `brain_action("fix-links")` MCP action. |
| [v0.16.3](changelog/v0.16.3.md) | 2026-03-29 | Broken link prevention — New `check_broken_wikilinks` compliance check detects broken wikilinks (`warning`) and ambiguous wikilinks where the basename matches multiple files (`info`). |
| [v0.16.2](changelog/v0.16.2.md) | 2026-03-29 | MCP server robustness — Atomic JSON writes (temp+rename) prevent index/router corruption on crash. |
| [v0.16.1](changelog/v0.16.1.md) | 2026-03-29 | Namespace CSS snippet to `brain-folder-colours` — Renamed `folder-colours.css` → `brain-folder-colours.css` to avoid collisions when installing brain into existing vaults. |
| [v0.16.0](changelog/v0.16.0.md) | 2026-03-28 | **Release: [Infrastructure](changelog/releases/v0.16.0-infrastructure.md)** |
| [v0.15.12](changelog/v0.15.12.md) | 2026-03-28 | Artefact library manifests and schemas |
| [v0.15.11](changelog/v0.15.11.md) | 2026-03-28 | Callout section targeting for brain_edit |
| [v0.15.10](changelog/v0.15.10.md) | 2026-03-28 | Incremental index updates and staleness TTL for MCP server |
| [v0.15.9](changelog/v0.15.9.md) | 2026-03-28 | Shaping transcript topic switching and universal transcript linking |
| [v0.15.8](changelog/v0.15.8.md) | 2026-03-28 | Project subfolder standards, doc sync, version bump |
| [v0.15.7](changelog/v0.15.7.md) | 2026-03-28 | Canary maintenance: version gap, doc sync, singular type matching docs |
| [v0.15.6](changelog/v0.15.6.md) | 2026-03-28 | Accept singular artefact type keys across all MCP tools |
| [v0.15.5](changelog/v0.15.5.md) | 2026-03-28 | Remove design-proposals type, absorb into designs lifecycle |
| [v0.15.4](changelog/v0.15.4.md) | 2026-03-28 | Artefact library taxonomy improvements, design-proposals type, research definition rewrite |
| [v0.15.3](changelog/v0.15.3.md) | 2026-03-28 | Simplify review code fixes: move validation to check.py, clean up server.py |
| [v0.15.2](changelog/v0.15.2.md) | 2026-03-28 | MCP tool robustness: audit fixes, hardening, return type compat |
| [v0.15.1](changelog/v0.15.1.md) | 2026-03-28 | Broaden friction logs, add bug-logs artefact type |
| [v0.15.0](changelog/v0.15.0.md) | 2026-03-27 | Update docs, version bump to v0.15.0 for brain_process tool |
| [v0.14.12](changelog/v0.14.12.md) | 2026-03-27 | Add brain_process MCP tool with classify/resolve/ingest dispatcher |
| [v0.14.11](changelog/v0.14.11.md) | 2026-03-27 | Add process.py with classify, resolve, and ingest operations |
| [v0.14.10](changelog/v0.14.10.md) | 2026-03-26 | Add type description extraction and optional embedding support to build_index |
| [v0.14.9](changelog/v0.14.9.md) | 2026-03-27 | Fix upgrade to only copy changed files, preventing sync-service conflicts |
| [v0.14.8](changelog/v0.14.8.md) | 2026-03-27 | Match heading anchors, block refs, and embeds in wikilink updates |
| [v0.14.7](changelog/v0.14.7.md) | 2026-03-27 | Fix rename/delete to match filename-only wikilinks, not just full-path stems |
| [v0.14.6](changelog/v0.14.6.md) | 2026-03-27 | Add ingestions artefact type, update captures taxonomy |
| [v0.14.5](changelog/v0.14.5.md) | 2026-03-27 | MCP output polish (DD-026 cont.) — Bold past-tense action labels on confirmations (`**Compiled:**`, `**Created**`, `**Edited:**`, etc.) and proper `isError` flag via `CallToolResult` for error responses (enables error styling in MCP clients). |
| [v0.14.4](changelog/v0.14.4.md) | 2026-03-27 | MCP response readability (DD-026) — MCP tool responses now return plain text instead of JSON blobs for confirmations, list resources, and errors. |
| [v0.14.3](changelog/v0.14.3.md) | 2026-03-27 | Drop trailing space from tilde separator, align journal-entries, add prefixless migration |
| [v0.14.2](changelog/v0.14.2.md) | 2026-03-27 | Expand template vault defaults, remove Wiki |
| [v0.14.1](changelog/v0.14.1.md) | 2026-03-27 | Fix false conflicts on case-insensitive filesystems in migrate_naming |
| [v0.14.0](changelog/v0.14.0.md) | 2026-03-26 | Update docs, changelog, and version for generous filenames |
| [v0.13.5](changelog/v0.13.5.md) | 2026-03-26 | Add Writing _Published/ convention, expand template vault defaults |
| [v0.13.4](changelog/v0.13.4.md) | 2026-03-26 | Writing `_Published/` subfolder convention — published writing now moves to `Writing/_Published/` with date-prefixed filenames, mirroring the archiving standard's numbered workflow. |
| [v0.13.3](changelog/v0.13.3.md) | 2026-03-26 | Document target parameter in guide.md |
| [v0.13.2](changelog/v0.13.2.md) | 2026-03-26 | Add target parameter to brain_edit for section-level operations |
| [v0.13.1](changelog/v0.13.1.md) | 2026-03-26 | Backport vault people refinements + extrapolate to hub standard |
| [v0.13.0](changelog/v0.13.0.md) | 2026-03-26 | Add elicitation principle, expand hub pattern, promote defaults |
| [v0.12.5](changelog/v0.12.5.md) | 2026-03-26 | **Release: [Agent Bootstrap](changelog/releases/v0.12.5-agent-bootstrap.md)** |
| [v0.12.4](changelog/v0.12.4.md) | 2026-03-26 | Cookie skill — new SKILL.md for the cookies artefact type. |
| [v0.12.3](changelog/v0.12.3.md) | 2026-03-26 | People + Observations artefact types — two new artefact types. |
| [v0.12.2](changelog/v0.12.2.md) | 2026-03-26 | Add upgrade script and workspace registry |
| [v0.12.1](changelog/v0.12.1.md) | 2026-03-26 | Add upgrade script and workspace registry |
| [v0.12.0](changelog/v0.12.0.md) | 2026-03-25 | Add brain_session MCP tool for agent session bootstrap |
| [v0.11.10](changelog/v0.11.10.md) | 2026-03-25 | Rename _Attachments to _Assets with Attachments + Generated subfolders |
| [v0.11.9](changelog/v0.11.9.md) | 2026-03-25 | Add presentations artefact type to library |
| [v0.11.8](changelog/v0.11.8.md) | 2026-03-25 | Replace sys.exit with importlib.reload for MCP version drift |
| [v0.11.7](changelog/v0.11.7.md) | 2026-03-25 | Add captures artefact type to library |
| [v0.11.6](changelog/v0.11.6.md) | 2026-03-25 | Workspaces artefact type — new living artefact type in the artefact library. |
| [v0.11.5](changelog/v0.11.5.md) | 2026-03-25 | Include artefact type tokens in search title boost |
| [v0.11.4](changelog/v0.11.4.md) | 2026-03-25 | Use filename stem as search title instead of H1 heading |
| [v0.11.3](changelog/v0.11.3.md) | 2026-03-25 | Add BM25 title boosting to search index |
| [v0.11.2](changelog/v0.11.2.md) | 2026-03-25 | Add brain_read file resource, fix index.md tool list |
| [v0.11.1](changelog/v0.11.1.md) | 2026-03-25 | Add wikilink hygiene guidance to archiving and provenance standards |
| [v0.11.0](changelog/v0.11.0.md) | 2026-03-24 | Changelog for MCP privilege split |
| [v0.10.3](changelog/v0.10.3.md) | 2026-03-24 | Add read.py and rename.py scripts, refactor MCP server to thin wrapper |
| [v0.10.2](changelog/v0.10.2.md) | 2026-03-24 | Decompose extensions.md into granular files under standards/extending/ |
| [v0.10.1](changelog/v0.10.1.md) | 2026-03-24 | Doc updates for standards extraction and ideas changes |
| [v0.10.0](changelog/v0.10.0.md) | 2026-03-24 | Add init.py setup script, core skills system, and brain-remote skill |
| [v0.9.21](changelog/v0.9.21.md) | 2026-03-24 | Tighten versioning rule, clarify skill distinction in plugins.md |
| [v0.9.20](changelog/v0.9.20.md) | 2026-03-24 | Add naming convention standard, fix 4 non-conforming temporal naming patterns |
| [v0.9.19](changelog/v0.9.19.md) | 2026-03-24 | Extract shared utilities into _common.py, deduplicate 5 scripts |
| [v0.9.18](changelog/v0.9.18.md) | 2026-03-23 | Add version drift detection to MCP server |
| [v0.9.17](changelog/v0.9.17.md) | 2026-03-23 | Extend folder colours to graph view |
| [v0.9.16](changelog/v0.9.16.md) | 2026-03-22 | Add post-propagation canary for vault-side updates |
| [v0.9.15](changelog/v0.9.15.md) | 2026-03-22 | Add Memories system with compiler, MCP, and template vault support |
| [v0.9.14](changelog/v0.9.14.md) | 2026-03-22 | Add journals, canary system, standards infra, doc accuracy pass |
| [v0.9.13](changelog/v0.9.13.md) | 2026-03-22 | Move backups to `.backups/` — vault backups now live in a hidden dot folder at vault root instead of `_Config/Backups/`. |
| [v0.9.12](changelog/v0.9.12.md) | 2026-03-22 | Add self-adapting colour system |
| [v0.9.11](changelog/v0.9.11.md) | 2026-03-21 | check.py (DD-009) — router-driven vault compliance checker. |
| [v0.9.10](changelog/v0.9.10.md) | 2026-03-21 | User documentation — quick-start guide + full reference |
| [v0.9.9](changelog/v0.9.9.md) | 2026-03-21 | Compiler extracts status_enum + terminal_statuses from taxonomy files |
| [v0.9.8](changelog/v0.9.8.md) | 2026-03-21 | DD-009 check catalogue expansion + compliance_check.py cleanup |
| [v0.9.7](changelog/v0.9.7.md) | 2026-03-21 | Decision logs + friction logs temporal types |
| [v0.9.6](changelog/v0.9.6.md) | 2026-03-20 | Artefact library consolidation, snippets type, style.css for all 18 types |
| [v0.9.5](changelog/v0.9.5.md) | 2026-03-20 | Provenance convention, subfolders convention, 4 library entries, temporal blend migration |
| [v0.9.4](changelog/v0.9.4.md) | 2026-03-20 | Documentation accuracy pass — 8 issues resolved |
| [v0.9.3](changelog/v0.9.3.md) | 2026-03-20 | Archive date convention — archiveddate frontmatter + date-prefixed filenames |
| [v0.9.2](changelog/v0.9.2.md) | 2026-03-19 | User preferences convention + version bump to 0.9.2 |
| [v0.9.1](changelog/v0.9.1.md) | 2026-03-17 | _Archive convention for living artefacts |
| [v0.9.0](changelog/v0.9.0.md) | 2026-03-17 | Separate routing from system directives |
| [v0.8.1](changelog/v0.8.1.md) | 2026-03-17 | Idea graduation workflow codified — ideas get `status` (new/graduated/parked); designs get `status` (shaping/active/implemented/parked). |
| [v0.8.0](changelog/v0.8.0.md) | 2026-03-16 | Docs and version bump for Obsidian CLI integration |
| [v0.7.0](changelog/v0.7.0.md) | 2026-03-16 | **Release: [MCP Server](changelog/releases/v0.7.0-mcp-server.md)** |
| [v0.6.0](changelog/v0.6.0.md) | 2026-03-15 | BM25 retrieval index (Phase 1 hybrid retrieval) |
| [v0.5.0](changelog/v0.5.0.md) | 2026-03-15 | Compiled router foundation (DD-008, DD-016) |
| [v0.4.1](changelog/v0.4.1.md) | 2026-03-15 | Add _Attachments/ system folder for non-markdown files |
| [v0.4.0](changelog/v0.4.0.md) | 2026-03-15 | Lean router, taxonomy discovery, single-line install |
| [v0.3.0](changelog/v0.3.0.md) | 2026-03-15 | BREAKING — Drop version from .brain-core path |
| [v0.2.1](changelog/v0.2.1.md) | 2026-03-15 | **Release: [Foundation](changelog/releases/v0.2.1-foundation.md)** |
| [v0.2.0](changelog/v0.2.0.md) | 2026-03-15 | Breaking: Renamed core files — vaults referencing `.brain-core/v0.1.x/artefacts`, `.brain-core/v0.1.x/naming`, or `.brain-core/v0.1.x/principles` must update wikilinks. |
| [v0.1.1](changelog/v0.1.1.md) | 2026-03-15 | Fixed 12 inconsistencies across core, template-vault, and specification |
| [v0.1.0](changelog/v0.1.0.md) | 2026-03-14 | Initial release. |
