# Changelog

Follows [semver](https://semver.org/). Changes to vault structure (renamed/removed core files, changed folder conventions) are breaking and bump the minor version. Artefact library definitions (taxonomy, templates, schemas) are patch; features that change how artefacts are processed are structural.

## v0.28.8 — 2026-04-18

**Warn on broken wikilinks at create/edit time and extend `fix-links` with a single-file mode.** Every `brain_create` / `brain_edit` run now performs a per-file wikilink check after the write and reports broken, resolvable, and ambiguous targets inline. `fix-links` gains `path` and `links` params so a single artefact can be repaired without a vault-wide pass, and both tools accept a `fix_links` convenience flag that auto-rewrites resolvable links immediately after the write. The vault-wide checker excludes `_Config/` to stop template and skill placeholders producing false positives.

- Added `check_wikilinks_in_file` in `src/brain-core/scripts/_common/_wikilinks.py` — the shared per-file helper used by the compliance checker, the inline create/edit check, and the single-file fix-links path. Accepts a pre-built `file_index` and `temporal_prefixes` so batch callers avoid redundant vault walks.
- Refactored `check.py:check_broken_wikilinks` onto the helper and dropped `_Config/` (including skills workspace output) from the walk. Direct-basename ambiguity still reports as `ambiguous_wikilinks`; strategy-driven ambiguity keeps its prior `broken_wikilinks` classification so existing behaviour is preserved.
- `create.py` and `edit.py` attach a `wikilink_warnings` key to their return dict whenever the written artefact contains non-clean findings, and an optional `wikilink_fixes` key when `fix_links=True` auto-rewrites resolvable targets. Non-artefact resources are not checked inline.
- `fix_links.py` now exposes `scan_and_resolve_file`, `scan_file`, and `apply_fixes_to_file` alongside the vault-wide functions. The MCP `fix-links` action routes to the single-file path whenever `path` is provided; `links` narrows the fix to specific targets.
- `brain_create` and `brain_edit` MCP tools expose a `fix_links: bool = False` parameter and surface `⚠` warning / `✔ Wikilink fixes applied` blocks in the tool response. Warning formatting lives in the new `format_wikilink_warnings` / `format_wikilink_fixes` helpers in `_server_artefacts.py`.
- Added `src/brain-core/standards/wikilinks.md` covering the core rule (only wikilink to targets that exist), the brain-managed scope boundary, the fix-links interface, and the file-index trade-off. Listed the standard in `session-core.md` and referenced it from `guide.md`.

## v0.28.7 — 2026-04-17

**Restore library-type install path and add a status mode to `sync_definitions`.** A regression (commit 95df8f9, then 07e4f05) had removed the ability to install new artefact-library types into an existing vault under any flag combination. `sync_definitions --types X` now installs uninstalled types additively, while bare `sync_definitions` still refuses to install new types — preserving the guard that stopped the old bulk-install foot-gun.

- Restored `sync_definitions --types <type>` as an additive install for uninstalled library types. No `--force` required; `--force` is reserved for overwriting locally customised or conflicting files.
- Added `sync_definitions --status` (and `brain_action("sync_definitions", params={"status": true})`) — read-only classification of every library type into `uninstalled`, `in_sync`, `sync_ready`, `locally_customised`, `conflict`, plus a `not_installable` bucket for library-side errors.
- Documented the state taxonomy in `docs/functional/scripts.md` and rewrote the "Installing a type" section in `src/brain-core/artefact-library/README.md` with explicit install / status / sync examples.
- Expanded the `sync_definitions` docstring in the MCP `brain_action` tool to describe status mode and install-via-types.

## v0.28.6 — 2026-04-17

**Add a default `release` artefact type and finish status-aware artefact naming.** Brain now ships a `Releases/` root folder with per-project release records whose filenames change as the release moves from `planned` to `shipped`. The naming contract driving that behaviour is generalised: every artefact type can now declare a `### Rules` table keyed on frontmatter state and a `### Placeholders` table that backs custom placeholders with frontmatter fields.

- Added the `release` artefact-library definition with planned/active/shipped/cancelled lifecycle, version-aware filenames, gate checklists, and human-written changelog sections. Living types in the artefact library: 13 → 14.
- Promoted Releases to the starter vault defaults and updated the quick guide, getting-started guide, template library guide, specification, and colour/count tests. Starter vault defaults: 27 → 28 (10 → 11 living, 17 temporal). Library total: 33 → 34.
- Introduced the shared naming engine in `_common/_naming.py` — one source of truth for rule selection, filename render, validate, and reverse-parse. `brain_create`, `brain_edit`, `check`, `rename`, and `migrate_naming` all read from it.
- Formalised the canonical advanced `## Naming` form (Rules + Placeholders tables with a `*` wildcard cell grammar) in `src/brain-core/standards/naming-conventions.md`. The simple one-line form still works for types that don't branch on state.
- `brain_edit` now renames an artefact when frontmatter changes flip the active rule (e.g. a release going from `active` → `shipped` renames `Search Hardening.md` → `v0.28.6 - Search Hardening.md`). Wikilinks are updated in place.
- `migrate_naming` now preserves the original date for legacy temporal filenames when re-rendering through the current naming contract, so historical entries don't spontaneously move to today's month.
- Simplified the journal-entries taxonomy to the standard `yyyymmdd-journal~{Title}.md` form and deferred folder-scoping temporal artefacts by parent hub to a draft design.

## v0.28.5 — 2026-04-16

**Fix GitHub URL and install path examples across docs.** Corrected `robmorris` to `rob-morris` in all GitHub URLs and replaced hardcoded `~/brain` paths with generic `/path/to/brain` placeholders so the install command can be run interactively without proposing a default location.

## v0.28.4 — 2026-04-16

**Finish the short-term local runtime contract for `printables`.** `shape-printable` now resolves machine-local binary overrides before falling back to `PATH`, so vault installs can point at explicit `pandoc` and TeX binaries without changing the MCP action surface.

- Added `defaults.tool_paths` to the shipped config template and documented the machine-local override pattern in `docs/functional/config.md`.
- Updated `src/brain-core/scripts/shape_printable.py` to resolve `pandoc`, `xelatex`, `lualatex`, and `pdflatex` in this order: `BRAIN_*` env var, `.brain/local/config.yaml` `defaults.tool_paths`, then host `PATH`.
- Added coverage for configured tool paths and env-var precedence in `tests/test_mcp_server.py`.

## v0.28.3 — 2026-04-16

**Bring generated-asset workflows into line across printables, presentations, and mockups.** `shape-presentation` now matches its docs by rendering `_Assets/Generated/Presentations/{stem}.pdf` before optionally launching Marp preview, while the artefact-library docs now make the generated-assets convention explicit for `printables`, `presentations`, and `mockups`.

- Updated `src/brain-core/scripts/shape_presentation.py` to render a PDF, expose `render` / `preview` toggles, and return partial status with a clear warning when Marp is unavailable.
- Added test coverage for presentation PDF rendering and Marp-missing behaviour in `tests/test_mcp_server.py`.
- Documented the multi-file generated-assets convention: when one artefact owns several generated files, add `hub-slug` if needed and nest the outputs under `_Assets/Generated/{Type}/{OwnerType}~{hub-slug}/`.

## v0.28.2 — 2026-04-16

**Add a `printables` artefact type for page-based PDF exports.** Brain now has a non-default temporal type parallel to Presentations: markdown source lives in `_Temporal/Printables/`, PDF output goes to `_Assets/Generated/Printables/`, and a new `shape-printable` action creates the source artefact then renders it via pandoc.

- Added `src/brain-core/scripts/shape_printable.py` plus the `brain_action("shape-printable", ...)` MCP surface. The renderer auto-detects `xelatex`, `lualatex`, or `pdflatex`, and accepts `keep_heading_with_next` so section headings can reserve space before a page break instead of being stranded at the bottom of a page.
- Added the `printables` artefact-library type with taxonomy, template, skill, and LaTeX support files. The artefact library now contains 33 types total (13 living + 20 temporal).
- Updated the quick guide, specification, shaping skill routing, bounded-context docs, and tooling references for the new type and action.

## v0.28.1 — 2026-04-16

Hardening pass on the v0.28.0 vault registry:

- `vault_registry.py` serializes `register`/`unregister`/`prune` behind an `fcntl.flock` on a sibling `.lock` file so parallel installers can't lose entries on a load→modify→save race.
- `_config_home()` now falls back to `$HOME/.config` when `XDG_CONFIG_HOME` is unset, empty, *or* non-absolute, per the XDG Base Directory spec.
- `install.sh`'s `registry_single_valid_vault` now matches Python's `is_vault_root` — also accepts the legacy `Agents.md` marker, not only `.brain-core/VERSION`.
- Standardized all interactive prompts in `install.sh` to write to stderr (previously mixed — some prompts went to stdout).
- Dropped a dead same-path guard in `register()` and trimmed a couple of disproportionate docstrings; added tests for concurrent register and XDG relative-path/empty fallback.

## v0.28.0 — 2026-04-16

**Cross-vault registry at `~/.config/brain/vaults`.** `install.sh` now records every installed brain vault in a plain-text XDG-compliant registry. Running `bash install.sh` without a path offers the single registered vault for upgrade — no more "which directory did I install it in?". See [[Brain Versioning V2]].

- Added `src/brain-core/scripts/vault_registry.py` — stdlib-only helper with register/backfill/unregister/list/prune/resolve commands. Plain text format (`<alias>\t<path>`, one per line) so bash can read it directly without a Python dependency.
- Path resolves via `$XDG_CONFIG_HOME/brain/vaults`, defaulting to `~/.config/brain/vaults`. Chose XDG layout over `~/.brain/` to avoid namespace collision with vault-local `.brain/` directories and to follow standard Linux config conventions.
- Aliases are slugified from the folder basename; on collision a random `[a-z0-9]{3}` suffix is appended (same pattern as `_common/_templates.py`).
- Schema is deliberately minimal — per-vault metadata (version, timestamps) stays in each vault's own `.brain/`.
- `install.sh` hook points: register on fresh install, backfill-if-absent on upgrade, unregister on uninstall (called before `.brain-core/` is removed so the vault-local script is still reachable).
- Added integration tests covering the fresh-install → upgrade → uninstall round-trip.
- Strengthened the installer leak regression to prove stale source `template-vault/.mcp.json` and `.codex/config.toml` are replaced by freshly generated project-scoped client config during install.
- Minor bump because this introduces a new user-home artefact; no vault structure changes.

## v0.27.10 — 2026-04-15

**Style the root `_Archive/` folder.** Previously only `/_Archive` *subfolders* within artefact folders received slate styling — the top-level `_Archive/` fell through to the default look despite being a first-class vault concept. `_css_archive_section` now emits full slate treatment (fg + 12% bg + 4px double border) for root `_Archive/`, matching the `_Assets/` style. The `⧈` archive icon badge (specified in the Status Folders design but never wired up) is also now emitted by `_css_system_folder_icons`. Artefact subfolder archive behaviour is unchanged.

- Updated `colours.md` and template-vault style doc to describe both archive cases (root + subfolder) explicitly.
- Regenerated `template-vault/.obsidian/snippets/brain-folder-colours.css`; the diff also reflects accumulated drift from newly-registered artefact types since the last compile.
- Adjusted `test_compile_colours.py` for the renamed section header (`_Archive Subfolders` → `_Archive (root + subfolders)`).

## v0.27.9 — 2026-04-14

**Move workspace manifest to `.brain/local/workspace.yaml` — all fields are install-specific.** Every field in the manifest (slug, brain identity, artefact links, auto-tags) describes the relationship between a specific clone and a specific vault, so the file belongs alongside other machine-local state rather than at the `.brain/` root where it could be mistakenly committed.

- `session.py` now reads from `.brain/local/workspace.yaml` with a logged fallback to the legacy `.brain/workspace.yaml` location.
- `init.py` scaffolds the manifest at the new location and auto-migrates legacy manifests on next run.
- Updated DD-040, functional docs, user reference, and script module map for the new path.
- Added regression coverage for the legacy fallback (session) and automatic migration (init).

## v0.27.8 — 2026-04-14

**Make project-scope MCP activation explicit instead of pretending registration is sufficient.** `init.py` and the installer now warn that project scope outranks user scope only once the project-scoped MCP is active: Claude still needs a `/mcp` approval step, and Codex still needs the project trusted with the project-scoped `brain` enabled.

- `init.py` now inspects Claude's per-project approval state in `~/.claude.json`, warns when a project-scoped `brain` is unapproved or explicitly disabled, and qualifies both Claude and Codex precedence messaging so project scope wins once the project entry is active rather than merely written.
- Installer, README, getting-started, functional script docs, and the shipped guide now tell users to activate project-scope `brain` before trusting precedence claims: approve via `/mcp` in Claude, or trust/enable the project-scoped entry in Codex. Both client `mcp list` commands are treated as health checks rather than proof that the project-scoped server is serving calls.
- Added regression coverage for the new Claude approval warnings and the installer's revised post-install guidance.
- Note: the originating bug report claimed `init.py` was writing a bogus `enabledMcpjsonServers` key into `.claude/settings.local.json`; review of the code and git history confirmed that key was never written by `init.py`, so no removal was required.

## v0.27.7 — 2026-04-14

**Drop the last `mcp/` transport shims now that legacy installs are repaired during upgrade.** The temporary `.brain-core/mcp/proxy.py` and `.brain-core/mcp/server.py` bridges have been removed from the shipped engine, so fresh upgrades depend on the packaged `brain_mcp` transport plus the `v0.27.6` repair migration instead of runtime delegation through deprecated entrypoints.

- Removed the deprecated `.brain-core/mcp/proxy.py` and `.brain-core/mcp/server.py` compatibility shims and their dedicated regression tests.
- Exposed `mark_embeddings_dirty` on `ServerRuntime` so sibling MCP handlers can invalidate doc embeddings through the runtime adapter instead of reaching into `server.py` internals.
- Bumped `brain_mcp.proxy` from `0.3.0` to `0.3.1` so proxy drift messages report a concrete version transition instead of the hash-only `0.3.0+modified` suffix when the packaged proxy changes.

## v0.27.6 — 2026-04-14

**Repair legacy MCP registrations that still launch the pre-`brain_mcp` transport paths.** Upgrades from older installs could keep working only because `.brain-core/mcp/` shims were still present. A new migration now rewrites stale Brain MCP launch entries to `python -m brain_mcp.proxy`, updates the recorded `init-state` config alongside the live client config, and preserves unrelated MCP entries in the same files.

- Added `migrate_to_0_27_6.py` to scan known Brain-managed Claude/Codex config surfaces for legacy `.brain-core/mcp/proxy.py` / `.brain-core/mcp/server.py` launch shapes that still point at the current vault, then rewrite only the Brain entry to the packaged transport with the required `PYTHONPATH`.
- Project and local registrations repaired by the migration now also regain `BRAIN_WORKSPACE_DIR` when the workspace path is known from the recorded init state, so post-upgrade bootstrap stays aligned with the current workspace-aware session contract.
- Added regression coverage for direct migration repair of project and user-scope config plus an upgrade-path test proving the repair runs as part of a normal upgrade from `0.27.5`.

## v0.27.5 — 2026-04-13

**Make bootstrap workspace-aware and scaffold workspace-owned metadata during folder setup.** `brain_session` now carries raw workspace identity plus optional `workspace_record` / `workspace_defaults`, and folder-scoped `init.py` now scaffolds `.brain/workspace.yaml` with a stable slug and default workspace tag when the workspace does not already declare one.

- `init.py` now passes `BRAIN_WORKSPACE_DIR` through both Claude and Codex MCP registrations, and Claude's SessionStart hook now calls `session.py --workspace-dir ...` so bootstrap knows which workspace is calling.
- `brain_action("compile")` no longer reports a bogus `colours` count, `session.py` no longer carries the unused `compile_session()` wrapper, and `brain_read`'s formatter now special-cases only the `environment` response instead of carrying dead JSON branches.
- Restored the missing `template-vault/People/` and `template-vault/_Temporal/Observations/` folders so the starter vault, shipped taxonomy/templates, docs, and colour-count tests agree again on the default type set.
- Updated the functional docs, user reference, and script module map for the workspace manifest contract and added regression coverage across `init.py`, `session.py`, `brain_session`, and `brain_read`.

## v0.27.4 — 2026-04-13

**Rename the leading body-range target to `:body_preamble` and stop it before the first targetable section.** The earlier `:body_before_first_heading` spelling was too specific to headings and understated that callout sections are targetable too. `brain_edit` now uses `target=":body_preamble"` for the leading body range before the first heading or callout section, and explicitly rejects the old pre-release spelling with guidance to the new target.

- Added regression coverage so `:body_preamble` preserves leading callout sections and only replaces the untargetable preamble range.

## v0.27.3 — 2026-04-13

**Tighten follow-up fixes for shutdown logging and legacy `brain_edit` target rejection.** `_flush_log()` now also tolerates closed-stream `ValueError` during stdio teardown, so shutdown no longer logs a false crash when handlers flush after their stream is already closed. Legacy `target=":body"` is now rejected consistently for `delete_section` too, returning explicit guidance instead of falling through to a generic section-not-found error.

- Added regression coverage for closed-stream flush failures and the `delete_section(target=":body")` rejection path on both the script and MCP surfaces.

## v0.27.2 — 2026-04-13

**Tolerate closed stdio pipes during MCP server shutdown.** The server previously treated a clean stdio teardown as an unexpected crash when the client closed stderr before the server flushed its log handlers on `SIGTERM`. `_flush_log()` now ignores `BrokenPipeError`/`EPIPE` so shutdown stays clean in interrupted review and other stdio teardown paths.

- Added regression coverage for the log-flush path so one broken-pipe handler no longer prevents the remaining handlers from flushing.

## v0.27.1 — 2026-04-13

**Make whole-body `brain_edit` targeting explicit and reject legacy `:body`.** `brain_edit` and `edit.py` now use `target=":entire_body"` for explicit whole-body edits, appends, and prepends; add `target=":body_before_first_heading"` for the leading un-headed prose before the first heading during `edit`; and hard-error legacy `target=":body"` because it was too ambiguous and destructive.

- Successful `brain_edit(operation="edit", target=":entire_body")` confirmations now stay on one line and include old/new line counts so callers can verify replacement scope immediately.
- Reserved non-section targets skip surrounding-heading context output, while normal heading and callout targets keep the existing placement context.
- `append` and `prepend` with no body and no frontmatter changes are now rejected as no-ops even when a target is supplied.
- Added regression coverage across script helpers, the MCP surface, and non-artefact resources for the new reserved-target contract.
- Updated the MCP tool docs, user reference, and in-vault guide to reflect the shipped behaviour.

## v0.27.0 — 2026-04-13

**Support native multi-client MCP install for Claude Code and Codex, with a tightened installer contract.** `init.py` now has explicit `--client claude|codex|all` handling, writes each client's native project/user config surface, rejects Codex local scope explicitly, records registrations in `.brain/local/init-state.json`, and removes only recorded Brain-managed entries via `--remove`.

- `install.sh` now passes explicit vault and project paths into `init.py`, registers both clients by default when MCP setup is enabled, updates retry/verify guidance for both `claude mcp list` and `codex mcp list`, and removes recorded project MCP entries during uninstall instead of deleting whole config files.
- Breaking: installer prompt suppression is now spelled `--non-interactive`, and `install.sh` no longer forwards installer prompt suppression into `upgrade.py --force`. Same-version re-apply, downgrade, and migration rerun behaviour remain explicit `upgrade.py --force` operations.
- `src/brain-core/scripts/edit.py` now reuses Brain's standard same-folder 3-character suffix convention when a converted target filename would collide, preventing silent overwrites of same-titled temporal artefacts during type conversion.
- Added regression coverage for Codex TOML merge/remove behaviour, recorded init state, dual-client installer arguments, `--client all --local` partial-success handling, the renamed installer flag, the removal of upgrade-force pass-through, and conversion collisions that previously overwrote same-day temporal targets.
- Updated README, getting-started, script references, the in-vault guide, plugin docs, contributor guidance, and the architecture overview to describe the current multi-client install contract.

## v0.26.5 — 2026-04-13

**Fix `brain_action("convert")` producing filenames that nest the source type's naming prefix inside the target pattern.** Converting a temporal artefact like `20260413-research~Foo.md` to `reports` previously produced `20260413-report~20260413-research~Foo.md` when the source had no `title` frontmatter. The raw filename stem was used as the title for the target pattern, duplicating the old prefix. `convert_artefact()` now extracts the clean title from the source filename using the source type's naming pattern before applying the target pattern.

- New helper `extract_title_from_naming_pattern(pattern, filename)` in `_common/_router.py` reverses a naming pattern to recover the `{Title}`/`{name}`/`{slug}` portion; returns `None` if the pattern is `None` or the filename does not match.
- Refactored `naming_pattern_to_regex()` and the new helper to share a single private `_build_pattern_regex()` builder and module-level placeholder tables, so new placeholder tokens now need one update rather than two.

## v0.26.4 — 2026-04-13

**Tidy the migration-ledger runner without changing its semantics.** `upgrade.py` now passes the in-memory migration ledger through its completeness checks instead of re-reading it from disk, and the migration-ledger regression tests now share a small helper for their generated counter migrations.

- `_run_migrations()` now returns both migration results and the current ledger so callers can reuse that state when deciding whether `.brain/local/.migrated-version` can be refreshed.
- `run_pending_migrations()` now passes `None` for `old_version` instead of the equivalent `"0.0.0"` sentinel.

## v0.26.3 — 2026-04-13

**Persist migration history in `.brain/local` so reinstalling `.brain-core/` does not replay old migrations.** `upgrade.py` now records successful and skipped migrations in `.brain/local/migrations.json`, refreshes `.brain/local/.migrated-version` once the installed version is fully accounted for, and consults that local ledger before running migrations again.

- `upgrade.py --force` now re-runs migrations up to the target version instead of only bypassing same-version / downgrade guards, and `install.sh --force` now passes that flag through during upgrade mode.
- Older vaults are backfilled into the new ledger from their installed version marker so adopting the per-migration ledger does not immediately replay historical migrations.
- Added regression coverage for migration-ledger skip behavior, force reruns, upgrade-time ledger backfill, and installer force pass-through.

## v0.26.2 — 2026-04-12

**Make installer MCP setup optional and non-fatal for agent-driven installs.** `install.sh` now supports `--skip-mcp` / `--no-mcp`, and fresh installs or upgrades no longer abort after the vault is already created just because `pip install` or MCP registration failed.

- Fresh installs now keep the scaffolded vault in place when `.venv` dependency installation fails, skip MCP registration for that run, and print exact retry commands for finishing setup later.
- Upgrade-time dependency sync now behaves the same way: the core upgrade completes, then warns if MCP dependency sync failed instead of aborting the whole upgrade.
- Added install regression tests for the failing dependency path and the explicit `--skip-mcp` flag, and updated the README plus install docs to document network-restricted agent installs.

## v0.26.1 — 2026-04-12

**Harden fresh installs against leaked machine-local template state.** `install.sh` now excludes `.venv`, `.brain/local`, `.mcp.json`, and `.pytest_cache` from template-vault copies and strips them after fallback copies, so dirty local source checkouts cannot seed stale virtualenv entrypoints or generated state into a new vault.

- Fresh installs and upgrade-time dependency sync now run `.venv/bin/python -m pip ...` instead of invoking `pip` wrapper scripts directly, avoiding copied absolute shebangs from the source template venv.
- Added a regression test that simulates leaked `template-vault/.venv` and stale `.brain/local` artefacts in a local source checkout and verifies the installer finishes with a clean target vault.

## v0.26.0 — 2026-04-12

**Package the MCP transport as `brain_mcp/` and leave compatibility shims in `mcp/`.** The live proxy/server runtime and sibling MCP handler modules now live under `.brain-core/brain_mcp/`, while `.brain-core/mcp/proxy.py` and `.brain-core/mcp/server.py` remain as thin deprecated entrypoint shims for older launch configs.

- `init.py` now registers the MCP transport via `python -m brain_mcp.proxy ...` with `PYTHONPATH=.brain-core`, and install/upgrade dependency sync now reads from `.brain-core/brain_mcp/requirements.txt`.
- The old `mcp/` transport implementation files were removed after being copied into the packaged `brain_mcp/` runtime, and tests/import paths were updated to use `from brain_mcp import server`.
- Architecture, functional, and user docs now describe `brain_mcp/` as the MCP transport home while preserving the unified session-bootstrap contract introduced in v0.25.0.

## v0.25.1 — 2026-04-12

**Remove contributor-only workflow tiers from shipped bootstrap.** `session-core.md` no longer includes contributor workflow sizing or links to contributor-only workflow docs. The workflow standard now lives in `docs/standards/agent-workflow.md` alongside `canary.md`.

- `docs/contributing-agents.md` now points at repo-only contributor standards instead of duplicating the full workflow-tier table inline.
- This corrects the v0.24.11 documentation change, which mistakenly described contributor workflow policy as part of the runtime bootstrap shipped to every Brain agent.
- The architecture docs and pre-commit canary now also encode the audience boundary explicitly: shipped `.brain-core/` bootstrap docs are for normal vault agents, while repo contributor process belongs under `docs/`.

## v0.25.0 — 2026-04-12

**Unified session bootstrap.** `session.py` now owns one canonical session model rendered as both `brain_session` JSON and the generated markdown mirror at `.brain/local/session.md`. Static core bootstrap content now lives in `session-core.md`, `index.md` is a thin bootloader, and router compiles plus MCP bootstrap refresh the markdown mirror automatically.

- `.brain-core` bootstrap is now treated as atomic and version-bound: `compile_router.py` requires `session-core.md` and treats a missing file as a broken install instead of silently falling back to legacy `.brain-core` files.
- New migration `migrate_to_0_25_0.py` canonicalises installed bootstrap text to the MCP-first / `index.md` fallback wording and also catches the newer manually introduced variant without terminal punctuation.
- `brain_session` now exposes session-core references as structured `core_docs` entries with explicit `brain_read(resource="file", ...)` load instructions, while `.brain/local/session.md` renders them as markdown file links for local non-MCP flows.
- Bootstrap docs, contributor guidance, the quick-start guide, and the user reference were updated to describe the JSON + markdown parity contract and the current three-mode bootstrap ladder.
- `session-polyfill.md` is no longer part of the shipped runtime surface.

## v0.24.12 — 2026-04-12

**Extract MCP tool handlers behind sibling modules.** `mcp/server.py` now stays as the MCP composition root and runtime-state owner while tool implementation logic delegates to sibling modules (`_server_session.py`, `_server_reading.py`, `_server_artefacts.py`, `_server_actions.py`, `_server_content.py`) through a narrow `_server_runtime.py` adapter. This preserves the stable `server` module surface used by tests and the proxy while aligning the MCP layer more closely with the bounded-context map.

**Refresh MCP documentation for the current architecture.** Updates the architecture overview, bounded-context map, MCP ADR, and user reference to reflect the composition-root plus sibling-handler structure, correct the documented version-drift restart code to `10`, and fix the MCP tool count to eight.

## v0.24.11 — 2026-04-12

**Formalise the tiered workflow in agent instructions.** Adds an explicit `trivial` / `small` / `medium` / `large` execution model to the checked-in agent contract and the shipped `.brain-core` bootstrap. Agents now have a documented default for when to implement directly, when to plan first, and when to escalate to design approval and broader review.

## v0.24.10 — 2026-04-12

**Add a thin Gherkin layer for core domain behaviours.** Introduces `pytest-bdd` to the dev/test dependency surface and adds feature coverage for three core flows: artefact creation lifecycle, vault compliance checking, and router compilation. The new scenarios stay intentionally thin and exercise existing script APIs rather than duplicating the detailed unit-test suite.

## v0.24.9 — 2026-04-11

**Promote router and artefact helpers into the `_common` shared kernel.** Extracts compiled-router loading, artefact path validation, artefact naming and folder resolution, config-resource path building, and shared file reads into dedicated `_common` modules (`_router.py`, `_artefacts.py`) re-exported through the facade. This removes remaining helper imports between `create.py`, `read.py`, `start_shaping.py`, and compliance-adjacent callers while preserving public compatibility wrappers where tests depend on them.

**Update the script-layer module map for the new shared seams.** `src/brain-core/scripts/README.md` now documents the added `_router.py` and `_artefacts.py` modules and their place in the `_common/` dependency graph.

## v0.24.8 — 2026-04-11

**Explicit whole-section replacement mode for `brain_edit`.** Targeted `edit` now accepts `target=":section:..."` to replace a matched section including its heading or callout title line. The replacement body must begin with a heading or callout title line, enabling explicit heading renames and re-nesting without a whole-body edit.

**Targeted edit policy: strip copied wrappers, allow nested structure, still block structural replacement mistakes.** Plain targeted `edit` remains content-only. Exact copied outer heading/callout wrappers are stripped once to prevent duplicate headings. Leading callouts and lower-level headings are allowed as valid nested section content. Same-level or higher headings remain rejected and must use `target=":section:..."`.

**Document bounded contexts and import policy.** Adds an explicit architecture context map covering the 8 bounded contexts, their responsibilities, and practical import rules; links it from the architecture overview and mirrors the script-level ownership map in `scripts/README.md`.

## v0.24.7 — 2026-04-11

**Decompose `_common.py` into `_common/` package.** Splits the 1,140-line shared utilities monolith into 9 focused modules (`_vault`, `_filesystem`, `_frontmatter`, `_wikilinks`, `_markdown`, `_slugs`, `_search`, `_templates`) with `__init__.py` re-exporting all public names — consumers unchanged. Renames 3 private symbols to public (`FM_RE`, `INDEX_SKIP_DIRS`, `fenced_ranges`); updates `check.py` and `search_index.py`. Splits `test_common.py` into 7 module-matching test files with shared `conftest.py`.

**Tighten `_common` facade API boundary.** `__init__.py` now re-exports only supported public names. Two wikilink helpers with real cross-module consumers are promoted to public API (`discover_temporal_prefixes()`, `temporal_display_name()`); tests that validate internal helpers import from the owning submodule directly.

## v0.24.6 — 2026-04-10

**Session always-rule: prefer brain_list over brain_search for enumeration.** Adds an `Always:` rule to `index.md` directing agents to use `brain_list` / `list_artefacts.py` (not `brain_search`) when filtering by type, date range, or tag. Flows into `always_rules` in `brain_session` and is also visible to naive agents reading `index.md` directly.

## v0.24.5 — 2026-04-10

**MCP proxy writer thread cleanup.** Skips redundant proxy drift disk read when drift is already flagged. Non-BrokenPipe write errors now also terminate the writer thread (previously continued writing to broken stdout). Shutdown join guarded with `is_alive()` to avoid 5s stall when writer already exited via BrokenPipe.

## v0.24.4 — 2026-04-10

**MCP proxy: single-writer queue, universal drift decoration, eager drift detection.** All outbound client writes now go through a dedicated writer thread draining a queue — eliminates the latent thread-safety bug where the reader thread and main loop both wrote to `sys.stdout.buffer` without synchronisation. Proxy drift note now decorates error responses as well as success responses (`_decorate_with_drift_note` replaces `_inject_proxy_drift_note`). Drift detection runs eagerly on child exit — before `_drain_inflight` sends error responses — so the note appears on orphaned-request errors in the simultaneous server+proxy drift scenario. Proxy version bumped to 0.3.0.

## v0.24.3 — 2026-04-10

**MCP proxy reliability: replay, drift detection, health check.** Requests that trigger version-drift restarts are now transparently replayed to the new child — the client gets a success response instead of an error. Proxy drift detection uses file-hash comparison as fallback when `PROXY_VERSION` strings match, so on-disk proxy changes are detected even without a version bump. Reader thread replaces blocking `readline()` with `select()`-based I/O with configurable timeout (`BRAIN_PROXY_READ_TIMEOUT`, default 30s) — a child that hangs without exiting is killed after 3 consecutive timeouts with in-flight requests. `_read_with_timeout()` also switched from leaked daemon threads to select-based implementation.

## v0.24.2 — 2026-04-10

**Definition sync auto-applies safe updates.** `sync_definitions` no longer gates safe updates (upstream changed, no local changes) behind the `artefact_sync` preference — they always apply. Only conflicts (both sides changed) produce warnings. The upgrade output now correctly labels conflicts instead of calling all pending changes "Customised". The `sync_preview` dry-run-as-proxy-for-ask path is removed from `upgrade.py`; `"skip"` preference and `--no-sync` flag still disable sync entirely.

## v0.24.1 — 2026-04-09

**MCP proxy/server reliability fixes.** Version-drift detection now uses `os._exit(10)` instead of `sys.exit(10)` — anyio task groups wrap `SystemExit` in `BaseExceptionGroup`, causing the exit code to be lost and the proxy to shut down instead of restarting. Proxy now tracks in-flight request IDs and sends JSON-RPC error responses when the child server dies mid-request, preventing indefinite client hangs. Version-drift restart failure in the proxy now falls through to the backoff retry loop instead of leaving the proxy in a limbo state with no child and no retry.

## v0.24.0 — 2026-04-09

**Bootstrap streamlining.** Agent bootstrap now directs to `brain_session` + `[[.brain-core/index]]` instead of `brain_read(resource="router")`. `Agents.md` (the `CLAUDE.md` symlink target) carries the new directive in both the repo root and template vault. `init.py` gains a SessionStart hook that calls `session.py --json` automatically, and `ensure_claude_md()` now emits vault vs project bootstrap variants. `index.md` rewritten: 8 condensed principles (adds "Separate concerns", rewords "Be curious" → "Actively seek signal"), with links split into `session-polyfill.md` (core docs + standards) and `md-bootstrap.md` (non-MCP fallback). `brain-remote` core skill retired — its workflow is handled by `brain_session`. `taxonomy/readme.md` retired — taxonomy discovery goes directly via `_Config/Taxonomy/` (DD-018). Migration script `migrate_to_0_24_0.py` replaces known old bootstrap variants and removes the stale router directive.

## v0.23.6 — 2026-04-09

**Safe temp path generation.** New `make_temp_path()` utility in `_common.py` centralises temp file creation via `tempfile.mkstemp()` — cross-platform (Linux, macOS, Windows). `create.py` and `edit.py` gain a `--temp-path [SUFFIX]` flag that prints a safe writable path and exits, for agents calling scripts directly. MCP tool descriptions for `brain_create` and `brain_edit` updated with `mktemp` guidance so agents construct valid temp paths for `body_file`.

## v0.23.5 — 2026-04-09

**Shaping skill redesign and swarm-test multi-skill architecture.** The monolithic shaping skill is now five files: a parent router (`shaping`) that delegates to `assess` (shared setup and Q&A rules), `brainstorm` (new/stub artefacts), `refine` (convergent decision-driven shaping), and `discover` (exploration-driven artefacts like People and Ideas). Transcript format changes from `Q. > A.` to heading-based (`## session start`, `### Agent`, `### User`). `start-shaping` now appends to same-day transcripts instead of creating new files, and accepts a `skill_type` parameter for session heading labels. Swarm-test command updated to thin router referencing sub-skill files (`review`, `evaluate`).

## v0.23.4 — 2026-04-08

**Promote bug-logs, mockups, presentations to template vault defaults.** Update default type lists and counts across artefact-library README, specification, getting-started guide, and brain-core guide. Starter vault now ships 27 defaults (10 living + 17 temporal) out of 32 in the library.

## v0.23.3 — 2026-04-08

**Fix version-drift exit code for proxy restart.** `SystemExit(10)` from `_check_version_drift()` was caught by the `except BaseException` handler in `main()`, which fell through to `_shutdown()` — overwriting exit code 10 with 0. The proxy saw a clean exit and shut down instead of restarting. Now `SystemExit` is re-raised before the general handler runs, preserving the exit code for the proxy.

## v0.23.2 — 2026-04-08

**Wiki topic cluster guidance.** Wiki taxonomy now documents the master/sub-artefact subfolder convention for organising related wiki pages. Adds a Topic Clusters section covering when to split pages, how sub-pages are named (prefixed with parent topic), and the subfolder layout. Naming section updated with sub-page path examples.

## v0.23.1 — 2026-04-07

**scripts/README.md added.** Co-located functional-layer doc for the scripts directory. Covers the module table (all 24 scripts with purpose and CLI usage), dependency graph (which scripts import `_common`, which are standalone), shared patterns (`--json` flag, `--vault`/`find_vault_root()`, `main()` entry point), and the rule for adding new operations.

## v0.23.0 — 2026-04-07

**MCP proxy wrapper.** A thin stdio proxy (`proxy.py`) now sits between Claude Code and the brain MCP server. Upgrades no longer kill the MCP connection — the proxy detects the server's version-drift exit, relaunches it with new code, and replays the initialize handshake. Crash recovery with exponential backoff (0, 4, 8, 16, 32s) handles transient failures automatically. Proxy drift detection prompts users to restart via `/mcp` when the proxy itself is upgraded.

- `proxy.py` added to `.brain-core/mcp/` — new MCP entry point
- `server.py` simplified: `_check_and_reload` replaced with clean `sys.exit(10)` on version drift
- `init.py` updated: points `.mcp.json` at `proxy.py`, records init scope to `.brain/local/init-scope.json`
- **Migration:** existing vaults must re-run `init.py` to switch to the proxy launcher, then restart MCP via `/mcp`

## v0.22.8 — 2026-04-07

**Operator key generator.** New `generate_key.py` script wraps `hash_key()` from `config.py` to generate three-word operator keys and their SHA-256 hashes. Give the words to the agent operator; paste the hash into `config.yaml`. Supports `--count N` for generating multiple candidates. Uses `secrets.choice` for cryptographically secure randomness. Added `config.py` and `generate_key.py` to script listings in `user-reference.md` and `guide.md`.

## v0.22.7 — 2026-04-07

**Resource-scoped editing.** `brain_edit` gains a `resource` parameter (default `"artefact"`) for editing non-artefact resources in `_Config/`. Supports `skill`, `memory`, `style`, and `template` — same operations (edit, append, prepend, delete_section) with targeted section support. New `name` parameter for non-artefact resources; `path` remains required for artefacts only. Config resources skip artefact-specific behavior (no terminal status auto-move, no `modified` timestamp injection). Shared `config_resource_rel_path()` helper in `create.py` deduplicates path conventions between create and edit. Internal: extracted body operation helpers (`_apply_edit`, `_apply_append`, `_apply_prepend`, `_apply_delete_section`) and `_finish_artefact()` to reduce duplication across the four artefact functions. Part of the Unified Resource Interface design (Phase 5).

## v0.22.6 — 2026-04-07

**Fix auto-move nesting when re-terminating from a +Status/ folder.** `brain_edit` now moves files to the correct sibling `+Status/` folder (e.g. `+Superseded/`) rather than nesting inside the current status folder (e.g. `+Implemented/+Superseded/`) when changing a file's terminal status.

## v0.22.5 — 2026-04-06

**Graceful version drift error.** `_check_and_reload` now raises `RuntimeError` before exiting so the MCP client receives a meaningful "brain-core upgraded, please retry" error instead of a silent `-32000: Connection closed`. The process still exits via `os._exit()` after a 0.5 s delay, giving the framework time to flush the response.

## v0.22.4 — 2026-04-06

**Resource-scoped creation.** `brain_create` gains a `resource` parameter (default `"artefact"`) for creating non-artefact resources in `_Config/`. Supports `skill` (creates `_Config/Skills/{name}/SKILL.md`), `memory` (creates `_Config/Memories/{name}.md` with optional `triggers` frontmatter), `style` (creates `_Config/Styles/{name}.md`), and `template` (creates `_Config/Templates/{classification}/{Type}.md` — name is the artefact type key). New `name` parameter for non-artefact resources; `type`/`title` remain required for artefacts only. Resource names are slugified for filesystem paths. Duplicate detection via `safe_write(exclusive=True)`. New `create_resource()` dispatcher and shared `_create_config_resource()` helper in `create.py`. Part of the Unified Resource Interface design (Phase 4).

## v0.22.3 — 2026-04-06

**Superseded design status.** Adds `superseded` as a terminal status for designs that were valid but replaced by a different approach — distinct from `rejected` (declined) and `implemented` (fully built). Includes `+Superseded/` folder convention and callout template.

## v0.22.2 — 2026-04-06

**Resource-scoped search.** `brain_search` gains a `resource` parameter (default `"artefact"`) for searching non-artefact collections. Supports `skill`, `trigger`, `style`, `memory`, and `plugin` resources via text matching on name + file content. Artefact search unchanged (CLI-first with BM25 fallback). New `search_resource()` function in `search_index.py`. Part of the Unified Resource Interface design (Phase 3).

## v0.22.1 — 2026-04-06

**Read/list split and resource-scoped listing.** `brain_read` now requires `name` for all collection resources (type, trigger, style, skill, plugin, memory, workspace, archive) — calling without name returns an error directing to `brain_list`. `brain_list` gains a `resource` parameter (default `"artefact"`) for listing non-artefact collections, and an optional `query` parameter for text filtering. New `list_resources()` dispatcher in `list_artefacts.py` handles non-artefact listing from router collections. Archive listing logic extracted from `read.py` into shared `_list_archive()`. Part of the Unified Resource Interface design (Phase 2).

## v0.22.0 — 2026-04-06

**Write guards for brain_create and brain_edit.** New `check_write_allowed()` function in `_common.py` blocks writes to protected folders. Dot-prefixed folders (`.brain/`, `.brain-core/`, `.obsidian/`, etc.) are always blocked. Underscore-prefixed folders are blocked except `_Temporal/` (artefact storage) and `_Config/` (resource-typed operations). Guards are called from `create_artefact()` and `_open_artefact()` in edit.py, covering all edit operations. Defense-in-depth alongside existing vault containment and `.brain-core/` protection. Part of the Unified Resource Interface design (Phase 1).

## v0.21.9 — 2026-04-06

**MCP server robustness fixes.** `mcp.run()` now catches `BaseException` (not just `Exception`) so clean exits via `SystemExit` always log `shutdown: stdin closed`. Signal handler guards against `ValueError` on unexpected signal numbers. Startup error blocks now include stack traces (`exc_info=True`). Silent swallowing of version-check and `_surrounding_headings` errors replaced with `warning`-level log entries. Removed unused `timezone` import.

## v0.21.8 — 2026-04-06

**Null-means-delete for frontmatter fields.** Setting a frontmatter field to `null` in `brain_edit` now removes it from the artefact instead of writing a literal null value. Follows RFC 7396 merge-patch semantics. Works with all operations (`edit`, `append`, `prepend`, `delete_section`). Also fixes a bug where deleting `status` via null would orphan a `statusdate` field.

## v0.21.7 — 2026-04-06

**MCP server logging.** The server now writes persistent logs to `.brain/local/mcp-server.log` using Python's `RotatingFileHandler` (2 MB max, 1 backup). Startup diagnostics (version, vault path, compile/index timings), tool call tracing (name + duration), and shutdown/crash information are logged at INFO level. Tool call arguments are logged at DEBUG level, opt-in via `BRAIN_LOG_LEVEL=DEBUG`. Replaces all `print()`-to-stderr diagnostics with structured logging. Stderr still receives WARN+ messages for MCP client visibility.

## v0.21.6 — 2026-04-06

**Auto-set `statusdate` on status transitions.** `_merge_frontmatter` now sets `statusdate` (YYYY-MM-DD) whenever the `status` field actually changes value. Covers all edit operations. Explicit `statusdate` in `frontmatter_changes` takes precedence over the auto-set value.

## v0.21.5 — 2026-04-06

**Post-upgrade definition sync.** `upgrade.py` now runs `sync_definitions` after a successful upgrade. Behaviour follows the `artefact_sync` preference in `.brain/preferences.json`: `auto` applies safe updates, `ask` (new default) includes a preview for the caller to present, `skip` does nothing. CLI flags `--sync` / `--no-sync` override. Customised definitions are reported but never overwritten. Sync failures are captured — they never crash the upgrade.

## v0.21.4 — 2026-04-06

- **Fix:** MCP server version-drift restart — `sys.exit(0)` inside an asyncio coroutine was swallowed by the event loop, hanging the server and all connected clients. Replaced with `os._exit(0)`.
- **Fix:** MCP server startup timeout — compile and index build now run with a 30s timeout. If they hang (e.g. iCloud I/O contention), the server fails loudly instead of blocking indefinitely.
- **Feature:** Upgrade rollback — `upgrade.py` now backs up `.brain-core/` before modifying anything, runs compile as a validation step after copying, and restores from backup if copy or compile fails. Migrations only run after compile succeeds.
- **Feature:** Upgrade log — every upgrade attempt writes its result to `.brain/local/last-upgrade.json` for post-mortem diagnostics.

## v0.21.3 — 2026-04-06

- **Feature:** Documentation lifecycle — documentation artefacts now have status support: `new` → `shaping` → `ready` → `active` → `deprecated`. Default is `active`. Terminal status `deprecated` moves to `Documentation/+Deprecated/`. Migration backfills `status: active` on existing documentation artefacts.

## v0.21.2 — 2026-04-06

- **Fix:** `resolve_body_file` now accepts `/tmp` as a valid temp root on macOS, where `/tmp` symlinks to `/private/tmp` and differs from Python's `tempfile.gettempdir()` (`/var/folders/.../T`).

## v0.21.1 — 2026-04-06

- **Feature:** `brain_edit(operation="delete_section")` — deletes a section including its heading from an artefact. Requires `target` parameter. Uses `find_section(..., include_heading=True)` to splice out the heading and its content with clean blank-line handling.
- **Fix:** `brain_edit(operation="edit")` with `body=""` and a section `target` now produces a clean empty section (heading preserved, correct spacing) instead of leaving double-blank artifacts.

## v0.21.0 — 2026-04-06

- **Feature: Archive visibility.** `_Archive/` is now fully invisible to normal vault operations. Archived files are excluded from the vault file index, `resolve_artefact_path`, Obsidian CLI search results, and `brain_read`/`brain_edit`/`brain_list`. They are only accessible through dedicated archive operations.
- **Feature:** `brain_action("archive")` — archives a terminal-status artefact to `_Archive/{Type}/{Project}/` with date-prefix rename, archiveddate, and vault-wide wikilink updates.
- **Feature:** `brain_action("unarchive")` — restores an archived artefact to its original type folder, stripping the date prefix and removing archiveddate.
- **Feature:** `brain_read(resource="archive")` — list all archived files (no name), or read a specific archived file (with name).
- **Change:** Removed `resolve_broken_link` strategy 7 (archive_match). Broken links to archived files are now treated the same as links to deleted files.
- **Change:** `brain_read(resource="artefact")` and `brain_read(resource="file")` reject archive paths with a helpful error directing to `brain_read(resource="archive")`.
- **Change:** `brain_edit` rejects archive paths with a helpful error directing to `brain_action("unarchive")`.
- **Change:** Rewrote `standards/archiving.md` for top-level `_Archive/` and dedicated archive actions.
- **Change:** `check_archive_metadata` now also scans the top-level `_Archive/{Type}/` structure.
- **Migration:** `migrate_to_0_21_0.py` moves per-type `_Archive/` contents to the top-level `_Archive/`, preserving type/project structure. Idempotent.

## v0.20.1 — 2026-04-05

- **Fix:** `_Archive/` folders are now immune to auto-move side-effects. Previously, changing the status of an archived artefact could create `+Status/` folders inside `_Archive/`, and `convert_artefact` could silently move archived files to active type folders. Archived files can still be edited (for frontmatter maintenance) but auto-move and conversion are blocked.
- **Fix:** Migrations `0.19.0` and `0.20.0` now skip `_Archive/` directories, preventing accidental status rewrites or `+Status/` folder creation inside archives on fresh vault init.
- **Fix:** `check_broken_wikilinks` now skips `_Archive/` directories. Archived files are frozen snapshots — their internal wikilinks are not updated on rename, so broken-link findings from archives are expected noise, not actionable.

## v0.20.0 — 2026-04-05

- **Feature:** `brain_edit` now automatically moves artefacts to `+Status/` folders when setting a terminal status, and moves them back out when reverting to a non-terminal status. Wikilinks are updated vault-wide. Empty `+Status/` folders are cleaned up after the last file is revived. No separate `brain_action("rename")` call needed.
- **Change:** Writing taxonomy: replaced `## Publishing` with `## Terminal Status` so `published` is now handled by the auto-move system. The old manual 5-step publishing workflow is replaced by the standard terminal status auto-move, with date-prefixing and `publisheddate` as optional additional steps. `_Published/` convention replaced by `+Published/`.
- **Migration:** `migrate_to_0_20_0.py` moves existing terminal-status artefacts into their correct `+Status/` folders on upgrade.

## v0.19.7 — 2026-04-05

- **Security:** Removed `brain_action("upgrade")` from MCP server. Self-upgrading MCP servers are an anti-pattern — a prompt-injected agent could point upgrade at a crafted directory to replace `.brain-core/scripts/`. Upgrade is now CLI-only (`upgrade.py` from the repo, or `install.sh`). The MCP server detects version drift and exits cleanly for client restart.
- **Security:** `.brain-core/` is now write-protected from MCP rename/delete operations. Attempts to rename into or delete inside `.brain-core/` raise `ValueError`.
- **Change:** `upgrade.py` is excluded from vault copies (added to `IGNORE_FILES`). Existing copies will be cleaned up as "removed" files on next upgrade.
- **Change:** Replaced MCP server hot-reload with clean exit on version drift. Instead of reloading Python modules in-place (fragile, state-coupling risk), the server exits and lets the MCP client restart it fresh.

## v0.19.6 — 2026-04-04

- **Fix:** MCP tool handlers (`body_file`, rename, shape-presentation, upgrade) accepted unbounded filesystem paths, allowing reads/writes/deletes outside the vault. `resolve_body_file` now enforces vault-or-tmp boundary; auto-delete only fires for tmp files. Rename and shape-presentation validate paths stay within vault root. Upgrade rejects source directories without a `brain-core` path component.

## v0.19.5 — 2026-04-04

- **Fix:** MCP server now handles graceful shutdown per the stdio lifecycle spec. Handles SIGTERM/SIGINT with clean exit and logs shutdown reason to stderr, so clients can distinguish a normal shutdown from a crash on reconnect.

## v0.19.4 — 2026-04-04

- **Feat:** `compile_router` now emits `frontmatter_type` on every artefact dict. Configured artefacts use the singular type from their taxonomy frontmatter (`living/wiki`, `temporal/log`); unconfigured artefacts fall back to the folder-derived plural form. Enables downstream consumers to compare against the canonical type without digging into nested frontmatter.

## v0.19.3 — 2026-04-04

- **Refactor:** Retired `docs/standards/` subfolder — single-file folder wasn't earning its keep. `canary.md` moved up to `docs/canary.md`. References updated in `contributing.md` and `specification.md`.

## v0.19.2 — 2026-04-04

- **Fix:** Migration scripts failing during MCP upgrade due to stale module cache. `_run_migrations` now reloads `_common` and `rename` before executing migrations, so newly-added imports (e.g. `safe_write`) resolve against the freshly-copied files rather than the pre-upgrade cache.

## v0.19.1 — 2026-04-04

- **Refactor:** Unified template placeholder substitution — new `substitute_template_vars()` helper in `_common.py` handles `{{date:FORMAT}}` and custom variable replacement. `create_artefact()` gains `template_vars` parameter. `start_shaping.py` and `shape_presentation.py` refactored to use the shared helper.

## v0.19.0 — 2026-04-03

**Shaping standard** — single source of truth for iterative Q&A refinement of vault artefacts.

- New standard: `.brain-core/standards/shaping.md` — defines shapeable types, two flavours (convergent/discovery), the mechanical process, and completion criteria.
- New action: `start-shaping` — bootstraps a shaping session (sets status, creates transcript, links bidirectionally). Same-day collisions auto-suffixed.
- New skill: `.brain-core/skills/shaping/` — agent-facing shaping workflow (convergent and discovery flavours, session start, completion review).
- New helper: `unique_filename()` in `_common.py` — shared filename dedup for `brain_create` and `start-shaping`.
- Ideas lifecycle: `developing` → `shaping`, `graduated` → `adopted`, `+Graduated/` → `+Adopted/`. Migration automatic.
- Shaping transcript naming: `{sourcedoctype}-transcript` → `shaping-transcript`. Migration automatic.
- Multi-source transcript linking — transcripts can reference multiple source artefacts.
- Shaping support for 12 artefact types with defined quality bars and flavours (Designs, Ideas, Tasks, People, Plans, Presentations, Mockups, Cookies, Journal Entries, Reports, Research, Thoughts).
- Optional `shaping` / `ready` status added to 7 previously stateless types (no default — status only appears during/after shaping).
- Updated provenance standard: transcript linking for multi-source sessions, adoption documented as provenance pattern.

## v0.18.16 — 2026-04-04

- **Feature:** New `brain_list` MCP tool — exhaustive enumeration of vault artefacts by type, date range, or tag. Unlike `brain_search` (BM25, relevance-ranked, capped by `top_k`), `brain_list` filters the in-memory index directly and returns all matching artefacts up to a configurable cap (default 500). Parameters: `type`, `since`/`until` (ISO date strings), `tag`, `top_k`, `sort` (`date_desc`, `date_asc`, `title`). Added to all built-in profiles (`reader`, `contributor`, `operator`).

## v0.18.15 — 2026-04-04

- **Fix:** Router staleness detection now covers config resources (skills, plugins, styles, memories), not just artefact types. Previously, adding a new skill directory or memory file to disk while the server was running went undetected until something else triggered a recompile. New `resource_counts()` in `compile_router.py` centralises the discovery-to-router-key mapping; `count_memories()` provides a lightweight listdir-only count (skips trigger parsing).

## v0.18.14 — 2026-04-04

- **Feature:** `init.py` defaults to current directory (no flags needed) instead of vault root. `--local` flag uses gitignored `.claude/settings.local.json` + `.claude/CLAUDE.local.md`.
- **Refactor:** **Atomic safe writes across all scripts.** New `safe_write` / `safe_write_json` / `resolve_and_check_bounds` utilities in `_common.py`. All file writes now use tmp + fsync + `os.replace` for crash safety. Symlinks are resolved with bounds checking to prevent writes outside allowed directories. Self-contained scripts (`init.py`, `upgrade.py`, migrations) carry inline copies. Covers config files, vault content, compiled caches, and generated assets.

## v0.18.13 — 2026-04-04

- **Fix:** `resolve_artefact_path` now finds temporal artefacts by display name. Previously, looking up "Colour Theory" failed because the file index key includes the full dated prefix (`20260404-research~Colour Theory`). A fallback now strips `YYYYMMDD-type~` prefixes and matches the display-name portion. Applies to `brain_read`, `brain_edit`, and `brain_action`(convert) — all flow through the same resolver.
- **Refactor:** Extract `_temporal_display_name` and `_lookup_temporal_display_name` helpers, flatten control flow in `resolve_artefact_path`.

## v0.18.12 — 2026-04-03

- **Feature:** `brain_edit` gains a `prepend` operation — inserts content before a section heading (or at the start of the body). Agents can now place new sections before a target without knowing what precedes it.
- **Feature:** Operation-driven frontmatter merge — `append`/`prepend` extend list fields with dedup (e.g. adding tags), while `edit` overwrites. All three operations accept `frontmatter` for frontmatter-only mutations.
- **Feature:** Targeted edit/append/prepend responses now include surrounding heading context (`prev=## Alpha | next=## Gamma`) so agents can verify placement without re-reading the file.
- **Refactor:** Extract `_open_artefact`, `_merge_frontmatter`, `_save_artefact` shared helpers in `edit.py`, reducing duplication across edit/append/prepend.
- **Fix:** Unified no-op guard — all operations reject empty calls (no body, no frontmatter, no target) with a helpful error, replacing the edit-only guard.

## v0.18.11 — 2026-04-03

- **Fix:** `brain_action("rename")` no longer silently fails on cross-directory moves when Obsidian CLI is available. `obsidian_cli.move()` now detects error responses (returns `False` instead of `True`) and falls back to grep-replace. The server also pre-creates the destination directory before calling the CLI, matching the fallback path's behavior.
- **Refactor:** Extract `cli_available` pytest fixture for tests that need the Obsidian CLI path enabled, replacing 5 identical try/finally blocks.

## v0.18.10 — 2026-04-03

- **Fix:** `sync_definitions` no longer proposes installing new artefact types during upgrade. Previously, uninstalled types with no tracking and no target files generated `action: "new"` warnings; the guard that skipped them only applied in `force` mode. Extracted `_is_type_installed()` helper to make the intent explicit and prevent future divergence between force and non-force paths.

## v0.18.9 — 2026-04-03

- **Fix:** `convert_artefact` now preserves the hub parent subfolder when converting between living types. Converting `Ideas/Brain/foo.md` → designs now produces `Designs/Brain/foo.md` instead of `Designs/foo.md`.
- **Refactor:** `resolve_and_validate_folder` returns `(path, artefact_dict)` instead of just the path, eliminating a redundant router scan in `convert_artefact`.

## v0.18.8 — 2026-04-03

- **Fix:** Stop `built_at` advancing on incremental index updates. Previously, every `brain_create`/`brain_edit` moved the staleness threshold forward, making files created outside MCP invisible to the mtime-based staleness check.
- **Fix:** Remove early return from incremental update path so the TTL-gated staleness check can detect external files after incremental updates.
- **Fix:** Add document count check to index staleness detection. Files added or deleted outside MCP are now caught by count mismatch, not just mtime.
- **Fix:** Detect index version drift — stale index files from older brain-core versions trigger a full rebuild.
- **Refactor:** Split `_STALENESS_CHECK_TTL` into `_ROUTER_CHECK_TTL` (5s) and `_INDEX_CHECK_TTL` (30s) to reflect their different costs.
- **Fix:** Guard `_index_pending` with `threading.Lock` to prevent race conditions under concurrent MCP requests.

## v0.18.7 — 2026-04-02

- **Fix:** Guard `_check_router`, `_check_index`, and `_load_embeddings` against corrupted JSON cache files (valid JSON that isn't a dict). Previously caused uncaught `AttributeError` propagating through `brain_search`/`brain_read`.
- **MCP schema:** Add `Literal` type constraints to all enum-like tool parameters (`brain_read.resource`, `brain_edit.operation`, `brain_action.action`, `brain_process.operation`/`mode`). Agents now see `{"enum": [...]}` in the JSON schema instead of bare `string`, reducing misuse from guessed values.
- Consistent top-level `try/except` across all 7 MCP tools for graceful error handling. Previously only `brain_create`/`brain_edit`/`brain_process` had catch-all guards; now `brain_session`, `brain_read`, `brain_search`, and `brain_action` also catch unexpected exceptions.

## v0.18.6 — 2026-04-01

- **Refactor:** Extract `now_iso()` utility to `_common.py`; `edit.py` uses it instead of inlining `datetime.now(timezone.utc).astimezone().isoformat()`.

## v0.18.5 — 2026-04-02

- **Script-level timestamps (edit):** `edit_artefact()` and `append_to_artefact()` now update the `modified` frontmatter field on every write. The `created` field is never touched by edit operations.

## v0.18.4 — 2026-04-02

- **Refactor:** `create_artefact()` now captures a single `datetime.now()` and passes it to `resolve_naming_pattern()` and `resolve_folder()`, ensuring filename, folder path, and frontmatter timestamps all reflect the same instant. Both functions gain an optional `_now` parameter for testing.

## v0.18.3 — 2026-04-02

- **Script-level timestamps:** `create_artefact()` now injects `created` and `modified` ISO 8601 timestamps into frontmatter at write time. Both fields respect `frontmatter_overrides` (existing values are not overwritten). Eliminates the `front-matter-timestamps` Obsidian plugin dependency for artefacts created via script or MCP.

## v0.18.2 — 2026-03-31

- **Rename `brain_read` resources for clarity.** `resource="type"` (was `"artefact"`) lists artefact type metadata; `resource="artefact"` (was `"file"`) reads artefact content. Every resource is now named for what it returns.
- **New `resource="file"` smart resolver.** Resolves any vault file by name and delegates to the correct handler — artefact content, memory with trigger matching, skill with metadata, etc. Use when you don't know the resource type.
- **Helpful error for `_Config/` files.** When `resource="artefact"` basename resolution finds a file in `_Config/` (e.g. a memory or skill), the error suggests the correct dedicated resource instead of dumping the full artefact folder list.

## v0.18.1 — 2026-03-31

- Fix `sync_definitions --force` installing uninstalled artefact types. Force now only overwrites types already present in the vault.

## v0.18.0 — 2026-03-31

- **`+Status` folders for terminal-status artefacts** — artefacts reaching terminal status now move to `+Status/` folders within their type directory (e.g. `Designs/+Implemented/`, `Tasks/+Done/`, `Ideas/+Graduated/`, `Workspaces/+Completed/`, `Writing/+Published/`). These files remain searchable and indexed — no rename, no `archiveddate`. `_Archive/` is reserved for deliberate removal (soft delete, out of search).
- **`archived` removed from all status enums** — archiving is an action, not a state. Types that previously had `archived` as a status (workspaces, journals, people) now use `parked` for the "set aside" meaning.
- **`parked` standardised** — replaces `paused` in workspaces as the universal pause term across all artefact types.

## v0.17.4 — 2026-03-31

- **Fix `brain_edit` body wipe on frontmatter-only edits** — `edit_artefact` unconditionally replaced the body with the `body` parameter, so a frontmatter-only edit (`body=""`) would silently clear the entire document. Now: empty body with no target preserves existing content. Added `target=":body"` as a reserved keyword for explicit whole-body replacement (including clearing). The `:` prefix avoids collision with markdown headings named "body".

## v0.17.3 — 2026-03-31

- **Fix stale doc references** — updated 6 files still pointing to removed `extensions.md` (decomposed into `standards/extending/` in v0.13.0). Fixed check.py docstring claiming "8 structural rules" when there are 9 (broken wikilinks check was added post-DD-009).

## v0.17.2 — 2026-03-31

- **Dependency management for vault MCP server** — `install.sh` now reads from `.brain-core/mcp/requirements.txt` instead of hardcoding dependencies. Fresh installs and upgrades both use the requirements file. `upgrade.py` prints a dependency sync reminder when `requirements.txt` changes. Documented the pattern in `docs/tooling.md`.

## v0.17.1 — 2026-03-31

- **Fix `brain_read(resource="file")` for non-artefact paths** — full relative paths (containing `/`) now read any vault file directly, bypassing artefact folder validation. Bare basenames still resolve via wikilink-style lookup with artefact validation. Fixes confusing basename collision errors when reading taxonomy or config files (e.g. `_Config/Taxonomy/Living/designs.md` colliding with `_Config/Templates/Living/Designs.md`).

## v0.17.0 — 2026-03-30

- **Vault config system and operator profiles** — new `.brain/config.yaml` with two-zone model: `vault` zone (shared authority, profiles and operators) and `defaults` zone (customisable per-machine). Three-layer merge: shipped template defaults → `.brain/config.yaml` → `.brain/local/config.yaml`. Merge rules within defaults inferred from data type: scalars (local wins), booleans (either-true), lists (additive union).
- **Operator profile enforcement** — `brain_session` accepts optional `operator_key` parameter for SHA-256-authenticated operator identification. Three built-in profiles: `reader` (read/search only), `contributor` (read/search/create/edit/process), `operator` (all tools). Profile enforced per-call on all tools except `brain_session` (the auth entry point). No config = no enforcement (backward compatible).
- **Config loader** — new `config.py` script with `load_config()`, validation, and `authenticate_operator()`. Validates profile tool names, operator profile references, and default_profile existence.
- **Migration: preferences.json → config.yaml** — `migrate_to_0_17_0.py` moves non-default preferences into the new config format. Empty preferences just deleted. Canary-format companion `.md` for naive agents.
- **Session payload** includes config metadata (brain_name, available profiles, default_profile) and active_profile after authentication.

## v0.16.10 — 2026-03-30

- **Rewrite Obsidian CLI integration from subprocess to IPC socket** — connects directly to Obsidian's `~/.obsidian-cli.sock` instead of spawning the Obsidian binary as a subprocess. Eliminates 2-3s startup latency, dock icon flash, and stdout noise filtering. Availability detection is instant (socket existence check) and probed lazily on first tool call rather than blocking MCP server startup. Module moved from `mcp/` to `scripts/` (shared layer). Search returns file paths enriched from index; move returns success/failure.

## v0.16.9 — 2026-03-30

- **Add `living/task` artefact type** — brain-native tasks as persistent, queryable units of work. Tracks status (`open`, `in-progress`, `done`, `blocked`), optional kind/priority, and agent assignment with claim timestamps. Board-per-artefact pattern links task boards to designs and other artefacts via hub subfolders. Phase 1 of the Brain Task Management design — internal task artefacts only, no external tool integration.

## v0.16.8 — 2026-03-30

- **Normalise `.md` extension in artefact and file paths** — agents no longer need to include the `.md` extension when passing paths to `brain_edit`, `brain_read`, or other artefact operations. `resolve_and_validate_folder` and `read_file_content` now normalise upfront since all vault content files are `.md`.

## v0.16.7 — 2026-03-30

- **`body_file` parameter for `brain_create` and `brain_edit`** — agents can pass large body content via a temp file path instead of inline, keeping MCP call displays compact. The server reads the file, uses its content as the body, and deletes the temp file after successful operation. Mutually exclusive with `body`. Shared `resolve_body_file()` helper in `_common.py`. Also adds `--body-file` CLI flag to `create.py` and `edit.py` for parity.

## v0.16.6 — 2026-03-29

- **Fix section-targeted edit corrupting following headings** — `brain_edit` with a `target` section would concatenate replacement content directly with the next heading when the body lacked a trailing newline, corrupting the heading and making it invisible to subsequent section-targeted operations. Now normalizes spacing between replaced content and following sections.
- **Fix frontmatter list field data loss on round-trip** — `parse_frontmatter` and `serialize_frontmatter` only handled `tags` as a list field. Other multi-line list fields (`aliases`, `cssclasses`, etc.) were silently dropped during edit operations. Both functions now handle any list field generically. Parser rewritten as single-pass (was two-pass).

## v0.16.5 — 2026-03-29

- **Basename resolution for `brain_read` and `brain_edit`** — `brain_read(resource="file")` and `brain_edit` now accept basenames in addition to full relative paths, resolving them like wikilinks (case-insensitive, `.md`-optional). Exact paths still work; basename lookup is a fallback. Ambiguous basenames (matching multiple files) return all candidates. New `resolve_artefact_path()` utility in `_common.py`.
- **Basename disambiguation for `brain_create`** — when a new artefact's basename collides with an existing file in a different type folder, the filename is automatically disambiguated by appending the type key: `Three Men in a Tub (idea).md`. Same-folder collisions (duplicate title in same type) append a random 3-character suffix: `Three Men in a Tub k7f.md`. Replaces the previous warning-only approach.
- **Artefact library definition sync** — new `sync_definitions.py` script and `brain_action("sync_definitions")` MCP action. Three-way hash comparison (upstream vs installed vs local) syncs library definitions to vault `_Config/` files. Auto-updates unmodified files; preserves user customisations; returns warnings for conflicts and collisions. Per-file exclusions via `artefact_sync_exclude` in `.brain/preferences.json`. `force` flag overrides conflicts. Chains automatically after `brain_action("upgrade")`.

## v0.16.4 — 2026-03-29

- **Broken link auto-repair** — New `fix_links.py` script and `brain_action("fix-links")` MCP action. Scans for broken wikilinks and attempts resolution using 8 naming-convention heuristics (slug→title, double-dash→tilde, temporal prefix matching, path stripping, tilde-space normalization, archive matching). Dry-run by default; `--fix` / `{fix: true}` applies unambiguous fixes. New `resolve_broken_link()` utility in `_common.py` for single-link resolution.

## v0.16.3 — 2026-03-29

- **Broken link prevention** — New `check_broken_wikilinks` compliance check detects broken wikilinks (`warning`) and ambiguous wikilinks where the basename matches multiple files (`info`). New `extract_wikilinks()` and `build_vault_file_index()` utilities in `_common.py`. `brain_create` now warns when a new file's basename collides with an existing file. New `standards/linking.md` documents the wikilink contract: basename-only by default, collision avoidance rules, link maintenance procedures, and guidance for agents without MCP tools.

## v0.16.2 — 2026-03-29

- **MCP server robustness** — Atomic JSON writes (temp+rename) prevent index/router corruption on crash. Module hot-reload with snapshot/rollback prevents mixed old/new state on partial failure. Router and index ensure-fresh helpers catch errors gracefully (stale data beats no data). Startup wraps each subsystem independently so one failure doesn't prevent others from loading. `main()` catches fatal startup errors and exits cleanly.

## v0.16.1 — 2026-03-29

- **Namespace CSS snippet to `brain-folder-colours`** — Renamed `folder-colours.css` → `brain-folder-colours.css` to avoid collisions when installing brain into existing vaults. Updated compile script, template vault, install script, and all docs.
- **Preserve user graph colours across recompiles** — `write_graph_json` now merges brain-generated `path:` entries with user-defined colorGroups (tag:, file:, freetext queries) instead of replacing the entire array.
- **Install script hardening** — Stdin pipe detection with helpful `bash <(curl ...)` guidance, `--force` flag for non-interactive use, safety guards against system/home directories, existing-vault detection (installs brain-core without clobbering user files), spinner elapsed-time display with stderr capture, graceful fallback when Python <3.10.

## v0.16.0 — 2026-03-28

- **`.brain/` directory restructure** — Machine-local generated caches (compiled router, retrieval index, embeddings) move from `_Config/` dotfiles to `.brain/local/` (gitignored). Workspace registry moves from `.brain/workspaces.json` to `.brain/local/workspaces.json`. New vault-portable files: `.brain/preferences.json`, `.brain/tracking.json` (Phase 2 prep for artefact library sync). Split rule: `_Config/` is prose you edit, `.brain/` is data the system manages. Migration framework added (`scripts/migrations/`) — versioned scripts run automatically during upgrade. Template vault updated with `.brain/` seed files. All script path constants, MCP server startup, tests, and docs updated.

## v0.15.12 — 2026-03-28

- **Artefact library manifests and schemas** — Added `manifest.yaml` and `schema.yaml` to all 31 artefact type directories. Manifests provide machine-readable install mappings (source→target file paths, folders to create, router triggers). Schemas document the frontmatter tooling contract per type (required fields, optional status enums). Phase 1 of the artefact library sync system — no enforcement, documentation only.

## v0.15.11 — 2026-03-28

- **Callout section targeting for brain_edit** — `find_section` now supports Obsidian callouts as targets using the `[!type] title` syntax (e.g. `target="[!note] Implementation status"`). Edit replaces callout content, append inserts at end. Callouts inside fenced code blocks are correctly skipped. Docstrings updated across `server.py`, `edit.py`, and `_common.py`.

## v0.15.10 — 2026-03-28

- **Incremental index updates and staleness TTL** — `brain_create`/`brain_edit` now queue single-file upserts (`index_update`) instead of triggering full index rebuilds; destructive actions (rename/delete/convert) set a dirty flag for full rebuild on next search. Filesystem staleness checks for router and index throttled by 5-second TTL. Corpus stats recomputation deferred when batching updates. `build_index.py` refactored: extracted `parse_doc()`, `_recompute_corpus_stats()`, and `index_update()` (upsert semantics, replaces `index_add`). Version drift now auto-recompiles router and marks index dirty.

## v0.15.9 — 2026-03-28

- **Shaping transcript topic switching and universal transcript linking** — Shaping transcripts now explicitly require a new transcript when the conversation pivots to a different source artefact (topic switch = new transcript). Transcript linking promoted from design-specific convention to universal provenance pattern — any shaped artefact should list its transcripts. Router trigger sharpened to "after shaping each artefact" for boundary clarity.

## v0.15.8 — 2026-03-28

- **Project subfolder support for living artefacts** — `brain_create` gains optional `parent` parameter to place artefacts in `{Type}/{Project}/` subfolders (living types only; ignored for temporal). Archive validation (`check.py`) now scans `{Type}/{Project}/_Archive/` in addition to `{Type}/_Archive/`. New standards documentation for master/sub-artefact conventions (`subfolders.md`) and project subfolder archiving patterns (`archiving.md`). Doc sync across tooling, user-reference, and guide.

## v0.15.7 — 2026-03-28

- **Canary maintenance: version gap and doc sync** — Version bump to cover three previously unversioned brain-core commits: extract `slug_to_title()` to `_common.py` and remove dead code across 5 scripts; fix stale archiving examples in `standards/archiving.md`; simplify `process.py`/`build_index.py` (dead code removal, regex caching, IDF fix). Doc sync: add `brain_session` and `brain_process` to `index.md` tool list, expand script fallback lists in `index.md` and `guide.md`, fix stale `yyyymmdd-slug` naming example in `specification.md`. Document singular type key matching in `tooling.md` and `user-reference.md` (v0.15.6 cont.).

## v0.15.6 — 2026-03-28

- **Accept singular artefact type keys across all MCP tools** — Agents frequently pass singular forms like `"report"` instead of `"reports"`. Added `match_artefact()` helper to `_common.py` with normalised singular/plural fallback (strips trailing "s" from both sides). Applied consistently to `resolve_type()`, `read_artefact()`, `read_template()`, and `brain_search` type filter. Fixed `brain_create` docstring example from `"idea"` to `"ideas"`.

## v0.15.5 — 2026-03-28

- **Remove design-proposals, absorb into designs lifecycle** — Removed `design-proposals` temporal artefact type. Designs gain `proposed` and `rejected` statuses, absorbing the proposal workflow: a design at `proposed` status captures a candidate needing a decision, and a decision log records the accept/reject verdict. Design template gains a "Decision Needed" section. Updated cross-references in Idea Logs, Plans, Decision Logs, guide, and user-reference. Template vault updated accordingly.

## v0.15.4 — 2026-03-28

- **Artefact library taxonomy improvements** — Research definition rewritten: investigation and/or capture, "in depth" substance qualifier, broader source types. New `design-proposals` temporal artefact type for contemplated changes needing a decision before action — supports multi-target proposals and requires decision logs for acceptance. Four existing types (Designs, Idea Logs, Plans, Decision Logs) get cross-references and lifecycle chain enforcement. Idea Logs gain a `status` field (`open` / `graduated` / `parked`). Ingestions get enrichment boundary clarification and thread inventory principle; removed leaky "Step 6c" implementation reference. Reports get trigger section. Bug-logs: `->` → `→` arrow style. Research and Reports get one-line disambiguation in `user-reference.md`.

## v0.15.3 — 2026-03-28

- **Simplify review code fixes** — Moved `validate_artefact_folder/naming/path` from `edit.py` to `check.py` — validation belongs in the compliance module, not the mutation module; removes `read.py` → `edit.py` coupling. Removed unused `result` variables in workspace registry handlers. Added `check` to `_SCRIPT_MODULES` reload list so hot-reload picks up the moved validation functions.

## v0.15.2 — 2026-03-28

- **MCP tool robustness and hardening** — Full audit of all 7 MCP tools. Split `validate_artefact_path` into `validate_artefact_folder` (folder-only) and `validate_artefact_naming` (pattern-only); edit/append/convert now skip naming checks, fixing failures on existing files with non-conforming names. Added `except Exception` catch-all to all mutating handlers. Defensive fixes: `_fmt_search` score fallback, empty-body guard on `brain_edit`, shape-presentation params validation, migrate_naming null check. Fixed partial global state with atomic temp assignment. Deduplicated folder validation in `read.py`. Replaced per-call Obsidian CLI probe with TTL-gated `_refresh_cli_available()` (30s). Removed `-> str` return type annotations (MCP SDK 1.26.0 strict output validation rejects `list[TextContent]` and `CallToolResult` returns). Fixed stale vault-level taxonomy override for `temporal/logs` naming pattern (`log--yyyy-mm-dd.md` → `yyyymmdd-log.md`).

## v0.15.1 — 2026-03-28

- **Broaden friction logs, add bug logs artefact type** — Friction logs now capture any source of friction (inconsistencies, unintended outcomes, suboptimal experiences), not just missing context. New `bug-logs` temporal artefact type in the library for correctness failures that need resolution — distinct from friction because a single bug is individually actionable. Bug logs track status (`open` → `resolved`). Library-only; not a template vault default.

## v0.15.0 — 2026-03-27

- **Brain classification tools (v0.15.0)** — New `brain_process` MCP tool (7th tool) with three operations: `classify` (embedding → BM25 → context_assembly fallback for artefact type matching), `resolve` (filename + BM25 + embedding duplicate detection), `ingest` (full classify → resolve → create/update pipeline). Backed by `process.py` script and `build_index.py` extensions for type description extraction and optional sentence-transformers embeddings. Graceful degradation: works without numpy/sentence-transformers via BM25 or context assembly. Tooling, user-reference, and guide docs updated.

## v0.14.12 — 2026-03-27

- **Add `brain_process` MCP tool** — 7th MCP tool with operation dispatcher for `classify` (ranked type matching with confidence), `resolve` (duplicate detection via filename + BM25 + embedding search), and `ingest` (full classify → resolve → create/update pipeline). DD-026 compliant response formatting. Index auto-refreshes after successful mutations.

## v0.14.11 — 2026-03-27

- **Add process.py script** — New script module with `classify_content()` (embedding → BM25 → context_assembly fallback), `resolve_content()` (filename match + search-based duplicate detection), and `ingest_content()` (full classify → resolve → create/update pipeline). Also `infer_title()` for extracting titles from content.

## v0.14.10 — 2026-03-26

- **Type description extraction and optional embedding support** — `build_index.py` now extracts taxonomy descriptions (one-liner + Purpose + When To Use/Trigger sections) via `extract_type_description()`. Optional `build_embeddings()` encodes type descriptions and documents using sentence-transformers (all-MiniLM-L6-v2) with graceful degradation when dependencies unavailable. Server loads embeddings at startup and refreshes on index rebuild.

## v0.14.9 — 2026-03-27

- **Targeted copy in upgrade to prevent sync conflicts** — `upgrade.py` now copies only added and modified files instead of bulk-overwriting the entire `.brain-core/` directory. Unchanged files are never touched, eliminating race conditions that cause cloud sync services (iCloud, Dropbox, Google Drive, OneDrive, Syncthing) to create conflict copies.

## v0.14.8 — 2026-03-27

- **Wikilink matching now covers all Obsidian link forms** — heading anchors (`[[file#heading]]`), block references (`[[file#^id]]`), and embeds (`![[file]]`) are now matched and preserved during rename, delete, and convert operations. Extracted `make_wikilink_replacer()` helper and switched to named regex groups for maintainability.

## v0.14.7 — 2026-03-27

- **Fix rename/delete to match filename-only wikilinks** — `rename.py` only matched full-path wikilinks (e.g. `[[Wiki/topic-a]]`) but Obsidian's default format is filename-only (`[[topic-a]]`). Now matches both forms and preserves the original link format in replacements. Skips filename-only matching when the basename is ambiguous (multiple files with the same name). Same fix applied to delete and convert operations. Refactored shared logic into `resolve_wikilink_stems()` and `_iter_vault_md_files()`. Updated tooling docs to describe multi-stem wikilink matching behaviour.

## v0.14.6 — 2026-03-27

- **Add ingestions artefact type** — new temporal type for content decomposition processing records. Links captures to created artefacts via enrichment, thread inventory, and artefact tracking. Update captures taxonomy to document the capture/ingestion relationship. Template vault now ships with 23 defaults (9 living + 14 temporal) out of 31 in the library.
- **Align template vault naming convention** — migrate all 13 temporal taxonomy files from `--{slug}` to `~{Title}` tilde separator, matching brain-core artefact library. Update Rob vault shape test fixture to current state (11 living + 18 temporal).

## v0.14.5 — 2026-03-27

- **MCP output polish (DD-026 cont.)** — Bold past-tense action labels on confirmations (`**Compiled:**`, `**Created**`, `**Edited:**`, etc.) and proper `isError` flag via `CallToolResult` for error responses (enables error styling in MCP clients).

## v0.14.4 — 2026-03-27

- **MCP response readability (DD-026)** — MCP tool responses now return plain text instead of JSON blobs for confirmations, list resources, and errors. Search results use multi-block `TextContent` for structured rendering. Complex/nested responses (router, compliance, convert, upgrade, migrate_naming) still return JSON. Design doc and implementation in `server.py`.

## v0.14.3 — 2026-03-27

- **Drop trailing space from tilde separator** — temporal separator is now `~` (tilde only), not `~ ` (tilde + space). All taxonomy patterns, examples, scripts, standards, and tests updated.
- **Align journal-entries to tilde separator** — journal entry naming changed from `yyyymmdd-journal--{journal-slug}.md` to `yyyymmdd-journal~{journal-slug}.md`. Topic variant follows the same pattern.
- **Migrate prefixless temporal files** — `migrate_naming.py` now handles research, plans, and transcripts that predate type prefixes (e.g. `20260307-discord-animation-research.md` → `20260307-research~Discord Animation Research.md`). The type prefix is derived from the artefact's naming pattern.

## v0.14.2 — 2026-03-27

- **Expand template vault defaults** — add Designs, Documentation, Ideas, Projects, Writing (living) to starter set. Remove Wiki from defaults (still available in artefact library). Add `_Workspaces/` data bucket folder. Add router triggers for Captures and Reports. Template vault now ships with 22 defaults (9 living + 13 temporal) out of 30 in the library.

## v0.14.1 — 2026-03-27

- **Fix migrate_naming on case-insensitive filesystems** — case-only renames (e.g. `gizmo.md` → `Gizmo.md`) were falsely reported as conflicts on macOS HFS+/APFS. Now detects same-inode case renames and allows them.

## v0.14.0 — 2026-03-26

- **Generous filenames** — artefact filenames now preserve spaces, capitalisation, and unicode instead of aggressive slugification. `My Project.md` instead of `my-project.md`. New `title_to_filename()` utility in `_common.py`; `title_to_slug()` retained for hub tags only.
- **Temporal separator change** — temporal artefacts use `~` (tilde) instead of `--` between the type prefix and title. `20260324-plan~API Refactor.md` instead of `20260324-plan--api-refactor.md`.
- **Updated all taxonomy naming patterns** — living types: `{name}.md` → `{Title}.md`. Temporal types: `yyyymmdd-{prefix}--{slug}.md` → `yyyymmdd-{prefix}~{Title}.md`. Naming conventions standard rewritten.
- **Migration script** — `migrate_naming.py` renames existing vault files from old conventions to new, updating all wikilinks. Available via CLI and `brain_action("migrate_naming")`. Supports `--dry-run`.
- **Design spike: Artefact Naming and Title System** — design artefact capturing decisions on slug vs in-doc H1 title relationships, with open questions for future phases (H1 indexing, per-type conventions, sync).

## v0.13.5 — 2026-03-26

- **Promote 6 artefact types to template vault defaults** — Workspaces (living); Captures, Reports, Snippets, Thoughts, Shaping Transcripts (temporal, storage folder added). Template vault now ships with 18 defaults (5 living + 13 temporal) out of 30 in the library.

## v0.13.4 — 2026-03-26

- **Writing `_Published/` subfolder convention** — published writing now moves to `Writing/_Published/` with date-prefixed filenames, mirroring the archiving standard's numbered workflow. Publishing adds `publisheddate` to frontmatter and renames via `brain_action("rename")` for wikilink hygiene. Archiving section updated: superseded published writing moves from `_Published/` to `_Archive/`.

## v0.13.3 — 2026-03-26

- **Doc: guide.md mentions `target` parameter** — brain_edit entry in the quick-start guide now notes the optional `target` parameter for section-level operations.

## v0.13.2 — 2026-03-26

- **`target` parameter for brain_edit** — both `edit` and `append` operations now accept an optional `target` parameter (heading text). `edit` replaces only that section's content; `append` inserts at the end of that section instead of EOF. Matching is case-insensitive; include `#` markers (e.g. `"### Notes"`) to disambiguate duplicate headings at different levels. Headings inside fenced code blocks are ignored. Append normalizes whitespace (one blank line before next heading). New `find_section()` helper in `_common.py`. CLI gets `--target` flag.

## v0.13.1 — 2026-03-26

- **"Match the effort to the input" ingestion principle** — hub pattern standard and all hub taxonomies (people, projects, journals) now split ingestion into two paths: minimal input creates a minimal hub with no fuss; rich input decomposes into artefacts. Backported from vault-refined people taxonomy. Projects and journals also gain separate Contextual Linking and Create Temporals subsections matching the people taxonomy's structure.

## v0.13.0 — 2026-03-26

- **"Be curious, then capture" core principle** — new principle in index.md. Agents should actively seek signal: notice gaps, ask natural questions at natural moments, capture answers as artefacts. The vault improves by eliciting, not just recording.
- **Hub pattern elevated to operational standard** — hub-pattern.md now prescribes five behaviours: granularity (decompose into artefacts), temporal handshake (temporals feed the hub), contextual linking (weave, don't list), ingestion (temporals first, hub second), and elicitation (hubs are moments to be curious).
- **Projects taxonomy expanded** — temporal handshake, ingestion workflow (decompose into research/decisions/plans/ideas, create temporals first), contextual linking guidance.
- **Journals taxonomy rewritten** — journal hub is now a living summary of the stream's arc, not a passive container. Temporal handshake, ingestion decomposition, contextual linking.
- **People, Observations promoted to template vault defaults** — ship in starter vault alongside Cookies (already default). Cookie skill added to template vault.

## v0.12.5 — 2026-03-26

- **People taxonomy refinements** — backported from vault. Observation handshake section explains how observations feed the person card. Ingestion section documents how to decompose a narrative brief into the right artefact types. Template now includes starter sections (Who, How we know each other, Notes).

## v0.12.4 — 2026-03-26

- **Cookie skill** — new SKILL.md for the cookies artefact type. Codifies the cookie workflow: get excited (genuine, not performative — excitement sustains the feedback loop), ask why the cookie was awarded, log immediately via `brain_create`. Cookies are the Brain's highest-signal feedback mechanism; the skill ensures agents treat them that way.

## v0.12.3 — 2026-03-26

- **People + Observations artefact types** — two new artefact types. People (`living/person`) is a hub type for storing what you know about a person — preferences, relationship context, key facts — updated as things change. Observations (`temporal/observation`) captures timestamped facts, impressions, and things noticed. Generic by design: connect observations to any hub via tags (e.g. `person/alice-smith`, `project/my-app`). Hub pattern updated to include People alongside Projects, Journals, and Workspaces.

## v0.12.2 — 2026-03-26

- **Workspace registry** — workspace slug-to-path resolution for linked workspaces. New script: `workspace_registry.py`. Embedded workspaces resolve implicitly (`slug` → `_Workspaces/slug/`); linked workspaces store external paths in `.brain/workspaces.json` (machine-local, not version-controlled). MCP server loads registry on startup. New `brain_read` resource: `workspace` (list all workspaces or resolve by slug). New `brain_action` actions: `register_workspace` and `unregister_workspace` for managing linked workspace registrations. Hub artefact metadata (status, tags) enriches workspace listings. 566 tests (was 532)

## v0.12.1 — 2026-03-26

- **`upgrade` brain_action + standalone script** — in-place brain-core upgrade from a source directory. Copies files, diffs trees, removes obsolete files, reports added/modified/removed/unchanged. Version-aware: skips same-version, warns on downgrade (both overridable with `--force`). Supports `--dry-run` for preview. MCP action triggers post-upgrade module reload, router recompile, and index rebuild automatically. New script: `upgrade.py`. 532 tests (was 520)

## v0.12.0 — 2026-03-25

- **`brain_session` MCP tool** — new top-level tool for agent session bootstrap. Returns a compiled, token-efficient payload in one round trip: always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, memory/skill/plugin/style indexes. The server actively compiles this — stripping frontmatter from user files, condensing artefact metadata, merging environment state. Accepts an optional `context` parameter (forward-compatible stub for future context-scoped sessions). New script: `session.py`. 6th MCP tool (was 5). 520 tests (was 503)

## v0.11.10 — 2026-03-25

- **`_Attachments/` → `_Assets/` refactor** — replaced the flat `_Attachments/` bucket with `_Assets/` containing `Attachments/` (Obsidian attachment target, user-added files) and `Generated/` (tool-produced output, reproducible from source — `Presentations/`, `Mockups/`). CSS variables renamed `--theme-attachments-*` → `--theme-assets-*`. Graph colour entry updated. Template vault `attachmentFolderPath` now points to `_Assets/Attachments`. Convention change — vault migration required

## v0.11.9 — 2026-03-25

- **Presentations artefact type** — new temporal artefact type in the artefact library. Slide decks generated from markdown content using Marp CLI. First artefact-bundled skill: type ships with a companion `SKILL.md` (Marp workflow) and `theme.css` (Brain-themed slide styles). Theme provides title slides, callout classes (warning/info/caution), risk colours, and CSS grid card layouts
- **`shape-presentation` brain_action** — new `brain_action("shape-presentation", {source, slug})` creates a presentation artefact from the template and launches `marp --preview` for live browser preview. Agent iteratively edits slides while the user watches. New script: `shape_presentation.py`

## v0.11.8 — 2026-03-25

- **MCP server module reload on version drift** — replaced `sys.exit(0)` with `importlib.reload()` for all script modules. When brain-core is upgraded mid-session, the server reloads code in-process instead of trying to exit for restart. Fixes silent CSS generation failure when `brain_action("compile")` ran with stale code after a brain-core propagation. Import style changed from `from X import Y` to module-level imports so reloaded code takes effect immediately via attribute access
- **Auto-recompile on taxonomy changes** — read, search, create, and edit tool calls now detect new or modified taxonomy files and auto-recompile the router before proceeding. New types installed mid-session are picked up without an explicit `brain_action("compile")` call. Detection uses filesystem type count + source file mtime comparison against the cached router

## v0.11.7 — 2026-03-25

- **Captures artefact type** — new temporal artefact type in the artefact library. External material ingested verbatim (emails, meeting notes, Slack threads, data extracts), frozen on ingest. Uses `source` frontmatter field to record origin. No lifecycle — captures are immutable once created

## v0.11.6 — 2026-03-25

- **Workspaces artefact type** — new living artefact type in the artefact library. Workspace hub files link brain artefacts to bounded data containers (`_Workspaces/`) for non-artefact files (CSVs, data, scripts, outputs). Follows the hub pattern with `workspace/{slug}` tags. Two modes: embedded (data in vault) and linked (data in external folder via `.brain/config`). Lifecycle: `active` → `paused` → `completed` → `archived`

## v0.11.5 — 2026-03-25

- **Artefact type tokens in title boost** — search index now includes tokenised artefact type (e.g. `temporal/idea-log` → `temporal`, `idea`, `log`) in the boosted title field. Searching "log" now surfaces idea-logs and other log types even when "log" doesn't appear in the filename or body
- 1 new test (493 total)

## v0.11.4 — 2026-03-25

- **Title from filename, not H1** — search index now uses the filename stem as the document title (boosted field), not the first H1 heading. In Obsidian the filename is the canonical title — it's what users link to and search for. The H1 remains indexed at normal weight as part of body text. Fixes issue where structural filename info (e.g. type prefix `idea-log`) was invisible to search

## v0.11.3 — 2026-03-25

- **BM25 title boosting** — search results now boost documents where query terms appear in the title (3x weight). Fixes ranking issue where long documents with diluted term density outranked shorter documents with exact title matches. Title term frequencies stored in index (`title_tf`); backward compatible with older indexes
- 3 new tests (492 total)

## v0.11.2 — 2026-03-25

- **`brain_read` file resource** — new `file` resource type reads any artefact file by relative path (`brain_read(resource="file", name="Wiki/my-page.md")`). Closes the search→read gap: `brain_search` finds files, `brain_read(resource="file")` reads them. Path validated against compiled router — must belong to a configured artefact type folder (consistent with `brain_edit`). System/config files remain served by their dedicated resource types
- **`index.md` tooling line updated** — lists all 5 MCP tools (`brain_create` and `brain_edit` were missing after v0.11.0 privilege split)
- 6 new tests (489 total)

## v0.11.1 — 2026-03-25

- **Wikilink hygiene for archiving** — added guidance to `archiving.md` and `provenance.md` on disambiguating wikilinks when an archived file's slug matches its successor (path-qualified supersession callouts, renamed-identifier origin links)

## v0.11.0 — 2026-03-24

- **MCP tool privilege split (DD-025)** — expanded from 3 to 5 MCP tools for granular permissions. `brain_create` (additive, safe to auto-approve) and `brain_edit` (single-file mutation) split out from `brain_action`. Two new actions added to `brain_action`: `delete` (removes file, replaces wikilinks with strikethrough) and `convert` (changes artefact type, moves file, reconciles frontmatter, updates wikilinks vault-wide)
- **New scripts** — `create.py` (artefact creation with template/naming resolution) and `edit.py` (edit, append, convert, path validation)
- **New shared utilities** — `title_to_slug()` (unicode-aware slug generation) and `serialize_frontmatter()` (round-trips with `parse_frontmatter()`) added to `_common.py`
- **`delete_and_clean_links()`** added to `rename.py` — deletes a file and replaces wikilinks with strikethrough text
- 76 new tests (483 total)

## v0.10.3 — 2026-03-24

- **Script/MCP parity** — added `read.py` (query compiled router resources) and `rename.py` (wikilink-aware file renaming) to `.brain-core/scripts/`. All MCP tool logic now lives in scripts as importable functions; the MCP server is a thin wrapper that imports and caches in memory
- **MCP server refactored** — `brain_read` delegates to `read.py` resource handlers, `brain_action("rename")` delegates to `rename.py`'s `rename_and_update_links()`. Server docstring updated to document the scripts-as-source-of-truth pattern
- **Tooling docs updated** — new DD-023 (Script Architecture) section in `tooling.md`; agent reading flow in `specification.md` updated to list scripts as tier 2; `index.md` Tooling simplified (scripts abstract Obsidian CLI); `guide.md` and `user-reference.md` updated with new scripts and fallback chain
- 36 new tests (407 total)

## v0.10.2 — 2026-03-24

- **Decomposed `extensions.md` into granular files** — extension procedures (adding types, memories, principles) extracted to `standards/extending/`. Old `extensions.md` removed; `standards/extending/README.md` is the new lean index. Reduces token cost when agents need a single procedure
- **New `standards/extending/` directory** — procedural instructions (how to add things) grouped under `standards/`, separate from reference standards (naming, archiving, provenance rules)

## v0.10.1 — 2026-03-24

- **Two new core principles** — "Always link related things" and "Save each step before building on it" added to `index.md` principles section
- **Extracted operational standards from `extensions.md`** — provenance, archiving, hub pattern, subfolders, and user preferences moved to individual files in `standards/`. `extensions.md` now covers only vault-extension procedures
- **Standardised provenance references** — all artefact type taxonomies now reference `standards/provenance.md` instead of inlining the generic pattern. Type-specific extras remain inline
- **Extension procedures updated** — adding a new type now includes a step to reference provenance/archiving standards in the taxonomy
- **Ideas taxonomy rewritten** — purpose reframed from "scratchpad" to iterative shaping; `developing` status added between `new` and `graduated`; graduation and lineage language softened
- **Ideas template** — new sections: The Idea, Why This Matters, Detail, Questions
- **Idea-logs taxonomy** — "Graduation Path" renamed to "Common Progressions" with softer language; "Spinning Out" section generalised for any target type; naming example fixed to include `idea-log--` prefix per naming conventions

## v0.10.0 — 2026-03-24

- **init.py setup script** (DD-023) — `.brain-core/scripts/init.py` configures Claude Code to use the brain MCP server. Three scopes: local (vault), user (all projects), project (per-folder override). Prefers `claude mcp add-json` CLI, falls back to direct `~/.claude.json` / `.mcp.json` editing. Self-contained, idempotent, Python 3.8+ stdlib only
- **Core skills** (DD-024) — compiler discovers skills at `.brain-core/skills/*/SKILL.md` alongside user skills from `_Config/Skills/`. Core skills tagged `"source": "core"` in compiled router, user skills tagged `"source": "user"`. Core skills are system methodology — versioned with brain-core, not user-editable
- **brain-remote skill** — `.brain-core/skills/brain-remote/SKILL.md` teaches agents the remote-project workflow: session bootstrap via MCP, key differences from in-vault work, logging, search, and setup instructions
- **Router trigger** — template vault router gains `When working via MCP from an external project → brain-remote` conditional

## v0.9.21 — 2026-03-24

- **Contributor skills** — added `simplify-audit` skill in `.claude/commands/` for analysing brain-core simplification opportunities. Contributor skills are Claude Code commands checked into the dev repo, distinct from user skills that ship in vaults
- **Clarified user vs contributor skill distinction** — `plugins.md` now distinguishes vault-shipped user skills from dev-repo contributor skills; `specification.md` uses "user skills" where context is vault-instance config
- **Tightened versioning rule** — any change to `src/brain-core/` files gets a version bump, including doc-only edits. Updated `contributing.md` and pre-commit canary

## v0.9.20 — 2026-03-24

- **Naming convention standard** — created `standards/naming-conventions.md` documenting the temporal `yyyymmdd-{prefix}--{slug}.md` pattern and living artefact naming principles, linked from `index.md`
- **Fixed 4 non-conforming naming patterns** — Research (`yyyymmdd-research--{slug}.md`), Plans (`yyyymmdd-plan--{slug}.md`), Transcripts (`yyyymmdd-transcript--{slug}.md`), and Logs (`yyyymmdd-log.md`) now use type prefixes consistent with all other temporal types
- Updated taxonomy files, guide.md quick-reference table, user-reference.md, user-guide.md, template-vault taxonomies, and test fixtures to match new patterns

## v0.9.19 — 2026-03-24

- **Script deduplication** — extracted shared utilities (`find_vault_root`, `read_version`, `is_system_dir`, `scan_living_types`, `scan_temporal_types`, `parse_frontmatter`, `tokenise`) into `scripts/_common.py`. All 5 scripts import from this module instead of duplicating ~120 lines each. Fixed dead code in `parse_frontmatter` (unreachable `key == "tags"` branch after `continue`). Removed 3 unused functions (`read_version` in search_index.py and check.py, `is_system_dir` in search_index.py)
- **Deleted 23 vestigial `style.css` files** — `compile_colours.py` auto-generates all CSS from the compiled router; these hand-written files were never read by any script and drifted when types changed
- **Documentation tightening** — removed ~50 lines of CSS selector templates from `colours.md` (agents never write CSS; the generator does). Removed hardcoded colour table from `artefact-library/README.md` (auto-generated, was drifting). Eliminated `library.md` and integrated its content into `artefact-library/README.md`. Removed duplicate "Always:" section from `index.md` (restated principles already covered above). Fixed grammar error ("These distinction" → "This distinction"). Moved "Maintaining This Guide" from `guide.md` to `docs/contributing.md`
- **Completed mockups artefact type** — added missing `README.md` and `template.md` to `temporal/mockups/` (was the only type with just `taxonomy.md`)
- Updated pre-commit canary to remove `library.md` references and colour table cross-check
- Test suite: 353 tests (was 323) — 30 new tests for `_common.py` shared module

## Canary labelled log output — 2026-03-23

- **Labelled canary logs with sub-items** — canary briefs now use bracket IDs (`[1]`, `[4a]`) instead of markdown numbered lists. Log lines include the item's label (`[4a] Artefact type: skip, reason`) so logs are self-describing without cross-referencing the brief. Items with sub-items expand into individual labelled log lines. Hook extracts expected IDs directly from the brief's Items section
- **Skip reason guidance** — standard now instructs agents to write their own assessment rather than copying example reasons. Hook validates a reason is present but not its content
- Pre-commit hook rewritten to extract bracket IDs via `sed`/`grep`, validate `[id] Label: status` format, and accept optional indentation on sub-items

## v0.9.18 — 2026-03-23

- **MCP server version drift detection** — server reads `.brain-core/VERSION` at startup and checks it on every tool call. If brain-core was upgraded since startup (version on disk differs from loaded version), the server exits cleanly (code 0) so the MCP client restarts it with the new code. Prevents stale code from writing wrong outputs, missing new resources, or misinterpreting updated data
- Test suite: 323 tests (was 316) — 7 new tests covering version recording, drift detection, graceful exit on all three tools, and resilience when VERSION is missing

## v0.9.17 — 2026-03-23

- **Graph view colours** — `compile_colours.py` now generates `colorGroups` entries in `.obsidian/graph.json` alongside the CSS snippet. Graph nodes are coloured by folder type using the same colour assignments: system folders (Slate/Violet/Orchid/Rose), living artefacts (computed hex), temporal children (rose-blended hex), and archive (Slate override). Existing graph settings (scale, forces, display) are preserved — only `colorGroups` is replaced
- `generate()` and `main()` both produce graph output. `--dry-run` prints graph JSON after CSS. `--json` includes a `"graph"` key
- `colours.md`: new "Graph View Colours" section documenting query syntax, ordering semantics, and merge behaviour
- `docs/user-reference.md`: Colour System section updated with graph view colours and file locations
- `docs/specification.md`: Colour System section updated with graph view reference
- Test suite: 316 tests (was 298) — 18 new tests covering hex→decimal conversion, graph colour group generation (system/living/temporal/archive entries, ordering, determinism), graph.json writing (create/preserve/replace)

## v0.9.16 — 2026-03-22

- **Post-propagation canary** — `docs/canaries/post-propagation.md` covers vault-side updates after brain-core propagation: activity log, daily note, master design doc. Triggered from `agents.local.md` (machine-specific, not universal)
- `docs/contributing.md`: new "Agents.md vs agents.local.md" section — rule of thumb for what goes where
- `Agents.md`: local overrides description now mentions workflow triggers as a use case
- `standards/` moved to `docs/standards/`, `docs/canary.md` removed (unused index page — canaries link directly to the standard)

## v0.9.15 — 2026-03-22

- **Memories** — conditional context that agents load on demand when the user references a project, tool, or concept. Memories are declarative reference cards (not procedural skills) stored in `_Config/Memories/` with `triggers: [...]` in YAML frontmatter
- **Compiler:** `discover_memories()` scans `_Config/Memories/*.md` (excluding README), extracts triggers from frontmatter (inline `[a, b]` and list `- a` formats), adds `memories` key to compiled router JSON. Memory files tracked in source hash for invalidation
- **MCP:** `brain_read(resource="memory")` — list all memories (no name), or search by trigger (case-insensitive substring, fallback to exact filename match). `brain_action("compile")` summary now includes memory count
- **Template vault:** ships with `_Config/Memories/README.md` (dispatch doc + trigger table) and `brain-core-reference.md` (first memory — what brain-core is, key locations, how extensions work). Router conditional added: "When the user references a project, tool, or concept and you lack context → Memories README"
- **Memories vs skills boundary** documented across all layers: memories answer "what is it?" (context), skills answer "how do I do it?" (procedures). If a memory starts containing steps, agents should create a skill instead. Memories can reference skills but not replicate them
- **Naive agent path:** router → README → trigger table → memory file works without compiler or MCP
- `extensions.md`: new "Adding a Memory" section
- User docs updated: guide.md (vault structure, configuration table), user-guide.md (new Memories section), user-reference.md (memory format, trigger matching, MCP integration, system folders table)
- Test suite: 298 tests (was 284) — 14 new tests covering memory discovery, trigger parsing, README exclusion, full compilation, source tracking, MCP list/search/fallback/case-insensitive/substring/not-found, compile summary

## v0.9.14 — 2026-03-22

- **Journals** living artefact type — named journal streams that group personal journal entries via nested tags (`journal/{slug}`). Follows the Projects hub pattern. Lifecycle: `active` → `archived`. Suggested colour: lavender
- **Journal Entries** temporal artefact type — personal reflections, recollections, and life updates in the user's own words. Four creation workflows: casual sharing, directed creation, shaping/drafting, manual. Naming includes journal slug with optional topic slug: `yyyymmdd-journal--{journal-slug}--{topic}.md`. Lavender → rose (`#D4A8DB`)
- Artefact library: 9 → 10 living types (Journals), 12 → 13 temporal types (Journal Entries)
- **Canary system** — `docs/canary.md` describes the approach; `docs/canaries/pre-commit.md` is the pre-commit canary with versioning, changelog, routing table, and cross-check list. `.githooks/pre-commit` hook verifies `.canary--pre-commit` log file covers all items
- **`docs/contributing.md`** — contributor guide: documentation architecture, why drift happens, testing, multi-repo workflow, common pitfalls
- **`standards/` directory** — generic reusable patterns extracted from project-specific work. First standard: `canary.md` (the canary pattern, independent of brain-core)
- `CLAUDE.md` slimmed — points to canary for pre-commit workflow and contributing for context
- `specification.md` Documentation section updated with full file list including new contributor docs
- **Hub pattern** documented as a general convention in `extensions.md` and `specification.md` — living artefacts that group related artefacts via nested tags (`{type}/{slug}`). Generalises the pattern used by Projects and Journals
- `specification.md`: fixed stale temporal blend formula (steel → rose), updated "What Ships in the Starter Vault" to match current 11 template vault defaults, updated extension procedures to reflect auto-generated colours, added library types catalogue
- Documentation accuracy pass across all doc layers:
  - User guide: added Shaping Transcripts to starter set list, journaling scenario added to "A Day in the Life", vault overview updated
  - User reference: both type specs documented, extension procedures updated to reflect auto-generated colours (was still showing manual CSS steps from pre-v0.9.12)
  - Quick-start guide: type table now shows all 11 template vault defaults (was showing non-default types instead of defaults), library types listed separately
  - Artefact library README: added Mockups to temporal table and colour recommendations, fixed Plans lifecycle (was `draft/active/complete/abandoned`, now `draft/approved/implementing/completed`), install steps updated for auto-generated colours
  - `library.md`: install steps updated for auto-generated colours

## v0.9.13 — 2026-03-22

- **Move backups to `.backups/`** — vault backups now live in a hidden dot folder at vault root instead of `_Config/Backups/`. Dot folders are hidden from Obsidian's file explorer and consistent with `.brain-core/`, `.obsidian/`. `_Config/` is now purely configuration
- **Remove `_Config/Assets/`** — unused folder removed from template vault. Was consolidated into other locations in v0.9.x
- `compliance_check.py` updated: backup path → `.backups/`, added `.backups` to `ROOT_ALLOW`
- Template vault `.gitignore` updated to ignore `.backups/` instead of `_Config/Backups/`

## v0.9.12 — 2026-03-22

- **Self-adapting colour system** — `compile_colours.py` auto-generates `.obsidian/snippets/folder-colours.css` from the compiled router. Hues are distributed evenly across available colour space using HSL distribution with exclusion zones, eliminating manual colour picking and CSS editing
- **Algorithm:** 4 system colours occupy fixed exclusion zones (120° total), leaving 240° for auto-distribution. Living and temporal types get independent hue distributions; temporal colours are rose-blended. Deterministic — same type list always produces the same colours
- **MCP integration** — `brain_action("compile")` now regenerates both router and colours. Response includes colour count
- `compile_colours.py` CLI: `--json` (colour assignments), `--dry-run` (preview CSS), `--vault /path` (target vault)
- `colours.md` rewritten with algorithm documentation replacing the fixed palette table
- `extensions.md` simplified — "Adding a Living Artefact Folder" and "Adding a Temporal Child Folder" no longer require manual colour picking or CSS selectors (7 steps → 5 steps each)
- Per-type `style.css` files in artefact library marked as reference-only (colours are auto-generated)
- Template vault CSS regenerated from compiler output
- Test suite: 284 tests (was 237) — 47 new tests covering hue distribution, HSL→RGB conversion, rose blend, CSS rendering, full pipeline, Rob's vault shape (21 types), and CLI args

## v0.9.11 — 2026-03-21

- **check.py** (DD-009) — router-driven vault compliance checker. Reads the compiled router and validates all vault files against 8 structural rules: `root_files` (no content in vault root), `naming` (files match taxonomy pattern), `frontmatter_type` (type field matches folder), `frontmatter_required` (required fields present), `month_folders` (temporal files in yyyy-mm/), `archive_metadata` (archiveddate, yyyymmdd- prefix, terminal status), `status_values` (status matches enum), `unconfigured_type` (folder has no taxonomy). Never parses taxonomy markdown — all rules come from the compiled router
- Flags: `--json` (structured output), `--actionable` (fix suggestions), `--severity <level>` (filter). Exit codes: 0 clean, 1 warnings, 2 errors
- `brain_read(resource="compliance")` — MCP integration. Returns structured findings; `name` parameter filters by severity
- Naming pattern → regex converter handles all 8+ vault patterns: `{slug}`, `{Title}`, `{sourcedoctype}`, `yyyymmdd`, `yyyy-mm-dd`, `ddd`, literal `--`, and combinations
- `compliance_check.py` docstring updated to clarify scope (session hygiene only; structural compliance is now check.py)
- Vault-maintenance SKILL.md updated with check.py reference
- Test suite: 175 tests (was 119) — 58 new tests covering all 8 checks, naming regex conversion, CLI args, output formatting, and full orchestration
- **Living artefact naming** — changed from `{slug}.md` (lowercase-hyphenated) to `{name}.md` (freeform — spaces and mixed case allowed). Applies to: Wiki, Zettelkasten, Designs, Ideas, Projects, Writing, Documentation. Temporal types and special patterns (Notes, Daily Notes) unchanged
- **`{slug}` regex widened** — now allows double-dash separators (`--`) for temporal types like Research and Plans (e.g., `20260317-gap-assessment--brain-inbox.md`)
- **Plans lifecycle** — added `implementing` status: `draft` → `approved` → `implementing` → `completed`
- **Mockups** temporal artefact type — visual/interactive prototypes generated to explore design directions. Naming: `yyyymmdd-mockup--{slug}.md`. Added to artefact library and documented in user reference
- **`--vault` flag** for check.py — point at any vault without needing to `cd` into it
- **Test infrastructure** — `pyproject.toml` (Python >=3.10, pytest config with `pythonpath`), `Makefile` (`make venv`, `make install`, `make test`, `make clean`). Removed `sys.path` hacks from all 6 test files. Fixed flaky `test_obsidian_cli` (mock servers switched to `serve_forever`, body draining on POST handlers). Test suite: 237 tests, all passing

## v0.9.10 — 2026-03-21

- **User documentation** — three-tier docs: quick-start guide (`src/brain-core/guide.md`, ships as `.brain-core/guide.md`), example-driven user guide (`docs/user-guide.md`), and full reference (`docs/user-reference.md`). The user guide walks through a day in the life of using the Brain, idea graduation, building knowledge, and how agents fit in. The reference documents all 20 artefact types with full frontmatter schemas, every configuration point, colour system, extension procedures, and tooling
- `CLAUDE.md`: added user documentation maintenance convention — update docs when changes affect user-facing behaviour
- **Cookies** temporal artefact type — a measure of user satisfaction, awarded when work lands well. Cookie dough → rose (`#DDA793`). Agents prompt honestly after meaningful work
- **Template vault defaults expanded** — 4 → 11 types. Added: Daily Notes, Notes (living); Research, Decision Logs, Friction Logs, Shaping Transcripts, Cookies (temporal). Router triggers added for all new temporal types
- Artefact library: 11 → 12 temporal types (Cookies added). 7 types newly marked as template vault defaults
- **Design Transcripts → Shaping Transcripts** — renamed to better describe the activity (refining any artefact through Q&A, not just designs). Library, docs, and references updated

## v0.9.9 — 2026-03-21

- **Compiler: status enum and terminal status extraction** — `compile_router.py` now extracts `frontmatter.status_enum` and `frontmatter.terminal_statuses` per artefact type from taxonomy files. Recognises three status enum patterns (inline YAML comment, lifecycle table, prose line) and cross-references archiving sections against the enum. check.py (DD-009) consumes these fields — no taxonomy parsing needed at check time
- Test suite: 119 tests (was 104) — 15 new tests covering status enum extraction (3 patterns + none + priority), terminal status detection (explicit refs, cross-reference, false positive rejection, edge cases), and integration through parse_taxonomy_file and compile

## v0.9.8 — 2026-03-21

- DD-009 check catalogue expanded: `archive_metadata` and `status_values` checks — 6 → 8 checks
- DD-009 contract made explicit in `tooling.md`: check.py reads compiled router only, never taxonomy markdown — compiler is the single adaptation point for vault evolution
- `tooling.md`: clarified relationship between `compliance_check.py` (session hygiene) and `check.py` (structural compliance)
- `compliance_check.py`: replaced hardcoded `EXPECTED_FOLDERS` with minimal infrastructure check (`_Config`, `_Temporal`) — eliminates false positives for vaults without `Wiki` or `_Plugins`

## v0.9.7 — 2026-03-21

- **Decision Logs** temporal type — point-in-time records of decisions capturing the "why" behind choices (question, options, tradeoffs, reasoning, implications). Sage → rose (`#B2BEA1`)
- **Friction Logs** temporal type — signal accumulator for maintenance: logs missing context, conflicting info, or assumptions. Recurring patterns distil into gotchas. Sky → rose (`#AFB2DB`)
- Artefact library temporal table: 9 → 11 types
- Colour recommendations table: 2 new entries (Decision Logs, Friction Logs)

## v0.9.6 — 2026-03-20

- Artefact library consolidation — `src/artefact-types/` moved into `src/brain-core/artefact-library/`, now ships to vaults as `.brain-core/artefact-library/`
- **Snippets** temporal type — short crafted content pieces (X posts, blurbs, descriptions) derived from existing work via provenance. Gold → rose (`#EBC49E`)
- 4 missing type definitions added to library: Writing, Zettelkasten, Thoughts, Reports (these types existed in vaults since v0.9.2–v0.9.5 but lacked library entries)
- `style.css` added to all 18 artefact type definitions — suggested default CSS for each type's colour variable and folder selectors
- `library.md` rewritten from duplicated catalog → short agent guide pointing to `[[.brain-core/artefact-library/README]]`
- Artefact library README expanded: "Choosing a Knowledge Type" guidance, colour recommendations table, updated structure diagram with `style.css`, install steps updated with style merge step

## v0.9.5 — 2026-03-20

- Artefact provenance convention — `Origin:` link on new artefact, `[!info] Spun out to` callout at top of source artefact body. Terminal status + archive when authority transfers fully
- Subfolders within living artefact folders — organic growth convention for when a single work spans multiple files. Index file, inherited type, automatic CSS coverage
- New principle: "Start simple, grow organically" added to `index.md`
- **Writing** living artefact type — rose colour, lifecycle: draft → editing → review → published → parked. Subfolder evolution cue for complex projects
- **Idea Logs** temporal type — blush → rose (`#ECB2B7`), tag: `idea`
- **Thoughts** temporal type — mint → rose (`#C2D2CC`), tag: `thought`
- **Reports** temporal type — lime → rose (`#D4D29E`), tag: `report`
- Temporal blend formula migrated from steel (`#8AA8C8`) to rose (`#F2A8C4`) — all existing temporal colour blends updated (Plans, Research, Daily Notes)
- `specification.md`: documentation section added

## v0.9.4 — 2026-03-20

- Documentation accuracy pass — 8 issues resolved across core and project docs
- `tooling.md`: `check.py` marked as designed-but-not-implemented (was documented as if built)
- `tooling.md`: `create_artefact` noted as potentially superseded by planned `write` action
- `tooling.md`: compiled router schema clarified as implementation-defined
- `tooling.md`: removed stale Rob vault reference; procedures directory added to Pending Design
- `library.md`: new "Choosing a Knowledge Type" section — wiki vs zettelkasten vs notes guidance
- `extensions.md`: new "When to Add a New Type" criteria section

## v0.9.3 — 2026-03-20

- Archive date tracking — `archiveddate: YYYY-MM-DD` frontmatter field added when archiving living artefacts
- Date-prefixed archive filenames — archived files renamed to `yyyymmdd-slug.md` for chronological sorting in `_Archive/` folders
- Wikilinks handled automatically by `brain_action("rename")` (Obsidian CLI first, grep-replace fallback)
- `extensions.md`: archiving steps expanded (3 → 5) with archiveddate and rename
- Designs/Ideas taxonomy: archiving workflows updated with new steps
- `index.md`, `specification.md`: archive descriptions updated

## v0.9.2 — 2026-03-19

- Zettelkasten artefact type — auto-maintained atomic concept mesh. One card per concept (~200–400 words), dense links to sources and related ideas. Graph maintained by deterministic maintenance layer; card content by separate enrichment step. Suggested colour: mint (`--palette-mint`)
- Wiki taxonomy: new "Relationship with Zettelkasten" section documenting the two-layer semantic graph (fine-grained concept mesh + coarse-grained knowledge base)
- Library: zettelkasten entry added under Living Examples
- User preferences convention — `_Config/User/preferences-always.md` for standing instructions, `_Config/User/gotchas.md` for learned lessons. Agents read both every session when present
- `index.md`: new "User Preferences" section directing agents to read user config
- `extensions.md`: new "User Preferences" section documenting the convention
- Template vault: empty `_Config/User/` files included
- `specification.md`: `_Config/User/` documented in Core/Config Split and folder listing

## v0.9.1 — 2026-03-17

- `_Archive/` convention for living artefacts — when a document reaches a terminal status (e.g. `implemented`, `graduated`), set the status, add a supersession callout, and move to `{Type}/_Archive/`
- Designs taxonomy: archiving section with `implemented` as terminal status, supersession callout format, agent contract
- Ideas taxonomy: step 7 added to graduation workflow (move to `Ideas/_Archive/`), archiving section
- `extensions.md`: new "Archiving Living Artefacts" section documenting the general pattern
- `index.md`: `_Archive/` subfolder note added to Key Idea section
- `colours.md`: archive subfolder styling template (slate, same as `_Attachments/`)
- Template vault CSS: wildcard `_Archive` selectors using slate theme variables
- `specification.md`: `_Archive/` note in Artefact Model section

## v0.9.0 — 2026-03-17

- Router restructured: always-rules and tooling instructions moved to `index.md` (version-bound, system-level); router becomes pure routing (conditional triggers only)
- All agents now read `index.md` every session via router directive — MCP-only agents no longer miss taxonomy-first gate, system principles, or tooling fallback chain
- Compiler reads always-rules from `index.md` (system) + `router.md` (vault-specific, optional), merges both into compiled router
- New `## Tooling` section in `index.md`: MCP → CLI+scripts → wikilinks fallback chain
- Fixed stale references in `extensions.md` (router artefact tables removed v0.4.0, principles section doesn't exist)
- Fixed stale "How It Works" text in `index.md` (router no longer lists artefact types)
- Fixed typo in `index.md`: "principals" → "principles"

## v0.8.1 — 2026-03-17

- Idea graduation workflow codified — ideas get `status` (new/graduated/parked); designs get `status` (shaping/active/implemented/parked). Taxonomy instructions now document lifecycle, graduation process, and lineage conventions
- Convention: navigational wikilinks (origin, transcripts, related docs) belong in body text, not frontmatter — ensures reliable Obsidian backlinks, graph view, reading mode visibility, and BM25 indexing
- Idea logs taxonomy updated with "Spinning Out to an Idea" section documenting the spinout process
- Design template now includes body scaffold (Core Goal, Open Decisions table)
- `brain_search` results now include `status` field; new `--status` filter on CLI and MCP tool
- Router updated with full fallback chain (MCP → CLI + scripts → lean router)
- Tooling docs clarified: Obsidian CLI relationship to MCP server (DD-022)

## v0.8.0 — 2026-03-16

- Artefact type library at `src/artefact-types/` — complete, ready-to-install type definitions separate from the minimal template vault. 13 types: 7 living (wiki, daily-notes, designs, documentation, ideas, notes, projects) and 6 temporal (logs, plans, transcripts, design-transcripts, idea-logs, research). Each type includes taxonomy, template, and install instructions
- Optional Obsidian CLI integration (dsebastien/obsidian-cli-rest) — CLI-preferred, agent-fallback (DD-021)
- `brain_search` — CLI-first live search with BM25 fallback. Response now includes `source` field (`"obsidian_cli"` or `"bm25"`)
- `brain_action("rename")` — new action: rename/move files with wikilink updates. Uses Obsidian CLI when available (wikilink-safe), falls back to grep-and-replace + `os.rename`
- `brain_read("environment")` — now includes `obsidian_cli_available` field
- New module `obsidian_cli.py` — lightweight HTTP client for the Obsidian CLI REST endpoint (stdlib only, no new dependencies)
- Startup probes CLI availability and derives vault name from directory basename (overridable via `BRAIN_VAULT_NAME` env var)
- All CLI calls catch network/parse errors and fall through to existing logic — server never crashes due to CLI unavailability
- Test suite: 155 tests (was 99)

## v0.7.0 — 2026-03-16

- Brain MCP server at `.brain-core/mcp/server.py` — wraps compiled router + retrieval index as 3 MCP tools (DD-010, DD-011, DD-020)
- `brain_read` — safe resource lookup: artefact, trigger, style, template, skill, plugin, environment, router. Optional `name` filter reads file content for styles/skills/plugins/templates
- `brain_search` — BM25 keyword search with `type`, `tag`, `top_k` filters. Returns ranked `{path, title, type, score, snippet}` results
- `brain_action` — mutations: `compile` (recompile router), `build_index` (rebuild retrieval index). Returns status summary
- Auto-compile router on startup if stale — compares `meta.compiled_at` against source file mtimes (DD-014)
- Auto-build index on startup if stale — compares `meta.built_at` against `.md` file mtimes across type folders
- Staleness detection: missing/corrupt JSON files treated as stale, missing source files treated as stale
- Server imports script functions directly (never calls `main()`) to avoid `sys.exit()` on error paths
- Requires Python >=3.10 + `mcp` SDK (`requirements.txt` included)
- Serves via stdio transport (designed for `.mcp.json` integration)
- Test suite: 42 tests covering startup, staleness detection, all brain_read resources, search, actions, and caching

## v0.6.0 — 2026-03-15

- `build_index.py` — BM25 retrieval index builder, walks all .md files in living + temporal type folders, computes per-doc term frequencies and corpus stats, writes `_Config/.retrieval-index.json`
- `search_index.py` — BM25 search over the pre-built index, supports `--type`, `--tag`, `--top-k` filters, returns ranked results with path, title, type, score, and ~200-char snippet
- Hand-rolled BM25 scoring (k1=1.5, b=0.75) — zero external dependencies, Python 3.8+ stdlib only
- Tokeniser: lowercase, split on non-alphanumeric, strip tokens < 2 chars
- Frontmatter extraction: parses type, tags, status from YAML frontmatter; title from first `# ` heading
- Same folder discovery as `compile_router.py` (convention-based `is_system_dir`), extended with recursive `.md` file walk
- `--json` flag on both scripts for piping/MCP integration
- Vault `.gitignore` updated to exclude `_Config/.retrieval-index.json`
- `find_vault_root()` across all three scripts now checks cwd first, then walks up from `__file__` — allows running scripts from the dev repo against any vault
- Test suite: 56 tests covering vault discovery, frontmatter parsing, tokenisation, index building, search ranking, snippets, and CLI args

## v0.5.0 — 2026-03-15

- `compile_router.py` — foundation script that compiles vault config into `_Config/.compiled-router.json` (DD-008, DD-016)
- Filesystem-first artefact discovery: root-level folders → living types, `_Temporal/` subfolders → temporal types
- System folder convention: any folder starting with `_` or `.` is excluded from living type scan; `_Temporal/` children scanned separately as temporal types
- Taxonomy parsing: extracts naming, frontmatter, trigger, and template sections from each type's taxonomy file
- Taxonomy lookup: tries exact folder name first, falls back to lowercase key — case-safe on all filesystems
- Trigger merging: router conditionals merged with taxonomy `## Trigger` sections, deduplicated by target path
- Enrichment discovery: skills (`_Config/Skills/*/SKILL.md`), styles (`_Config/Styles/*.md`), plugins (`_Plugins/*/SKILL.md`)
- Hash invalidation: SHA-256 of every source file, composite source_hash for staleness detection
- Version read from `.brain-core/VERSION` at runtime (no hardcoded version in script)
- All artefact paths relative to vault root (no absolute paths in output)
- `--json` flag for piping/MCP; default writes file + stderr summary
- Warning emitted when router.md yields zero rules (likely malformed section headers)
- Unconfigured types: `configured: false` with null fields (no inferred defaults)
- Test suite: 41 tests covering scanning, parsing, compilation, hashing, and template vault integration

## v0.4.1 — 2026-03-15

- Added `_Attachments/` system folder for non-markdown files (images, PDFs, etc.)
- Obsidian `attachmentFolderPath` set to `_Attachments` in app.json
- Slate colour styling for attachments folder (⧉ icon)
- System folder exclusion list updated across docs

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
