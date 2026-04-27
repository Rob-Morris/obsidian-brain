# Changelog

Follows a pre-1.0 [semver](https://semver.org/) policy: backward-compatible changes are patch; breaking Brain changes are minor; only fundamental model changes are major. Breaking Brain changes include vault-structure changes and breaking tool/script/MCP contract changes. Fundamental model changes are changes to the artefact model, router contract, or agent bootstrap/entry flow.

## v0.32.9 — 2026-04-27

**Tighten current-vault MCP repair ownership and make uninstall preserve non-Brain Claude bootstrap content.** `repair.py mcp` now repairs only the project-scoped Brain clients that are already installed for the current vault, while uninstall cleans recorded vault-root Claude local state and strips Brain's bootstrap line from `CLAUDE.md` without deleting user content.

- `check.py` / `_repair_runtime.py` now ignore bootstrap-only `CLAUDE.md` and unrelated `.claude/settings.local.json` state when deciding whether current-vault project MCP drift exists, so scaffold-only vaults and valid single-client installs no longer report false `mcp_registration` drift.
- `repair.py mcp` no longer invents first-time project registrations on a bare scaffold and no longer adds a second client that is not already installed for the current vault.
- `install.sh --uninstall` now removes recorded Brain-managed Claude local state at the vault root, uses `init.py` to strip Brain's bootstrap line from `CLAUDE.md`, and only deletes that file when it becomes empty.
- Add regressions for single-client project health, no-op-on-scaffold MCP repair, vault-root local Claude uninstall cleanup, and preserving user-authored `CLAUDE.md` content during uninstall.

## v0.32.8 — 2026-04-27

**Restore the MCP proxy's "no blocking, no queuing" restart contract.** Restart/backoff now runs on a dedicated recovery thread, so the proxy keeps reading stdin while the child is recovering. Requests that arrive during backoff or initial-start failure now fail fast with soft retry errors instead of stalling silently in the pipe for up to the full backoff window.

- `brain_mcp.proxy` now starts a dedicated recovery thread, moves every restart sleep and attempt onto it, turns dead-child detection paths into non-blocking signals, wakes recovery waits promptly on shutdown, and surfaces a hard `MCP unrecoverable — ...` error if the recovery thread itself dies.
- Version-reset after give-up is now asynchronous: the triggering `tools/call` still receives the explicit `/mcp` guidance immediately, while the recovery thread re-checks `.brain-core/VERSION`, clears give-up on change, and restarts in parallel.
- Expand `tests/test_mcp_proxy.py` with non-blocking recovery coverage, concurrent-signal race coverage, shutdown wake-up coverage, recovery-thread crash surfacing, and the initial-start soft-error contract. Bump the packaged proxy version from `0.3.2` to `0.4.0`.

## v0.32.7 — 2026-04-27

**Close the MCP proxy's stranded no-child state and make failure transitions explicit.** All dead-child detection paths now funnel through one restart coordinator, initial child start failures enter the same backoff loop, and persistent restart failure now converges to the hard `/mcp` guidance instead of looping forever on soft "server restarting" errors.

- `brain_mcp.proxy` now catches child launch failures, routes EOF / pre-send `poll()` / `BrokenPipeError` / missing-child recovery through one shared restart path, and keeps retrying across the full backoff schedule before marking the session as given up.
- Add proxy regressions for dead child detected before send, `BrokenPipeError` during send, initial child-start failure recovery, and explicit give-up after repeated init-timeout restarts.
- Bump the packaged proxy version from `0.3.1` to `0.3.2` so proxy drift notes report the transport change cleanly.

## v0.32.6 — 2026-04-26

**Add `repair.py` as the explicit infrastructure recovery entry point and formalise the managed-runtime contract.** Brain now has a named repair surface for current-vault MCP/runtime recovery, router/index rebuilds, and local workspace-registry normalisation. `repair.py` bootstraps from any compatible Python 3.12+ launcher, repairs the vault-local `.venv` when needed, then hands off into that managed runtime for packageful work. `check.py` now emits exact repair commands in human output and structured `repair` hints in JSON/compliance results when it detects router, MCP, or local-registry drift.

- Add `.brain-core/scripts/repair.py` with first-cut scopes `mcp`, `router`, `index`, and `registry`, plus a bootstrap-safe/runtime split (`_repair_common.py`, `_repair_runtime.py`).
- `mcp` repair now converges into the vault-local `.venv`, syncs `.brain-core/brain_mcp/requirements.txt`, and repairs current-vault project MCP config/bootstrap/init-state without touching user-scope config.
- Add current-vault-only `registry` repair for `.brain/local/workspaces.json`, including normalisation/backup of malformed state; `router` and `index` repairs rebuild their compiled caches with explicit no-op/dry-run behaviour.
- Extend `check.py` and `brain_read(resource="compliance")` with additive repair guidance: exact commands in normal human output and structured `repair` objects in JSON/compliance results for router, MCP, and local-registry drift.
- Ship DD-043 to formalise the bootstrap-launcher vs managed-runtime boundary, and refresh script, user, and architecture docs around the new repair surface.

## v0.32.5 — 2026-04-26

**Add shortest-clearest-unique slug derivation and apply it in the unreleased v0.31.0 migration.** Slug helpers now produce a multi-token pair when the title supplies one, falling through to a single distinctive keyword and finally a random suffix. Living-artefact keys derived during the v0.31.0 key-backfill aim for ≤20 characters with a meaningful 1- or 2-word identifier instead of the previous 64-char title slugs, and the previously-fatal `design/brain` collision on shared hub tags is now adjudicated cleanly.

- Add `derive_distinctive_slug(title, taken)` in `_common/_slugs.py` — picks the clearest unique slug for a title, preferring a multi-token pair by shape, otherwise the longest single keyword. The `{keyword}-{suffix}` slug is reserved for collision resolution.
- Generalise `extract_slug_keyword` → `extract_slug_keywords(title, max_words, budget)`. `generate_contextual_slug` now produces 2-word keyword + suffix slugs by default. Drop the singular helper and the `SLUG_KEYWORD_MAX` constant.
- Apply the new derivation in the unreleased v0.31.0 migration: cap title-as-key acceptance at `SLUG_TITLE_KEY_LIMIT = 20` (longer titles route through `derive_distinctive_slug`), hard `is_valid_key` 64-char limit unchanged.
- Restructure `plan_key_backfill` into three explicit passes: reserve explicit + legacy keys up front, adjudicate ambiguous self-tag claims (must lose to title-derivation when more than one artefact in the same type tags itself with the same key, else iteration order would decide the winner), then derive remaining keys. Cuts one redundant `read_frontmatter` per file.
- Safe for vaults that somehow ran the earlier v0.31.0 draft directly: any pre-existing valid `key:` is preserved under `source=existing` and never re-derived. Worst case is a subset of artefacts keeping the older draft's uglier keys while the rest get the new shorter form — no breakage, no rollback needed.
- Tighten `is_valid_key` to require at least one alphabetic character; pure-numeric keys (e.g. `20260309`) are rejected with `INVALID_KEY`. Slug-derivation ranking deprioritises numeric tokens so date-like tokens fall behind real words. Documented in `standards/keys.md`, `standards/naming-conventions.md`, `dd-041`, and the four hub-type templates.
- Fix `_workspace_slug` in `init.py` to apply unicode→ASCII normalisation, matching the `title_to_slug` contract. Internal cleanup in `_common/_slugs.py`: shared `_to_ascii_lower` helper, bounded suffix-retry loop.

## v0.32.4 — 2026-04-26

**Collapse the four artefact mutation entry points and harmonise `delete_section_artefact`.** Internal refactor with one signature change: `delete_section_artefact`'s `target` parameter is now an optional kwarg so its public surface matches `edit_artefact` / `append_to_artefact` / `prepend_to_artefact`. All existing call sites already used `target=` as a kwarg, so no caller changes are needed; missing-target callers continue to hit the same contract validator error.

- Introduce `apply_to_artefact(operation, ...)` in `edit.py` as the single shared implementation of artefact body mutations. The four public wrappers reduce to one-line aliases that forward to it; the `delete_section` specials (body/scope nulling, frontmatter merge mode, synthesised result `scope="section"`) are centralised inside the shared function so both `edit_resource` and the CLI dispatcher pass `body` and `scope` through unconditionally.
- Replace the operation cascades in `edit_resource` and the direct CLI dispatcher with a single `apply_to_artefact(operation, ...)` call each.
- Thread `new_body` through `_maybe_restructure_living_ownership` to `_commit_with_possible_rename` and delete the now-unused `_read_body` helper, removing one disk round-trip on the ownership-change path and closing the small window where a foreign writer could leak content into the rename commit.

## v0.32.3 — 2026-04-26

**Retire legacy `find_section` helpers and tighten the `session-core.md` contract.** Internal cleanup: `session.py` now drives core-bootstrap parsing through the same `resolve_structural_target` resolver `brain_edit` uses, so duplicate or missing `## Core Docs` / `## Standards` sections in `session-core.md` surface loudly during `brain_session` instead of being silently swallowed.

- Migrate `session.py` off the legacy `find_section` helper to `resolve_structural_target`, drop the broad `try/except ValueError` swallows around `_extract_markdown_section` and `_strip_markdown_section`, and let the resolver's "not found" / "ambiguous" errors propagate to the bootstrap caller.
- Delete `find_section` and `_find_callout_section` from `_common/_markdown.py` (~80 lines) along with the redundant `TestFindSection` / `TestFindSectionIncludeHeading` blocks in `tests/test_edit.py` (~210 lines); literal-region coverage moves onto the resolver in `tests/test_markdown_structural.py`.
- Add `tests/test_session_core.py` as a CI guard that asserts `## Core Docs` and `## Standards` appear exactly once in the authored `src/brain-core/session-core.md` and resolve unambiguously via `resolve_structural_target`.

## v0.32.2 — 2026-04-26

**Tighten `brain_edit` after a simplify pass.** Internal cleanup with one user-visible correction: the documented `path` resolution surface for `brain_edit` no longer overstates display-name support, so clients only see resolution paths the resolver actually implements.

- Narrow the `brain_edit` `path` description in the MCP tool docstring, `docs/functional/mcp-tools.md`, and `docs/user/user-reference.md` so it advertises canonical key, vault-relative path, filename basename, and the temporal-only display-name fallback. Previous "basename/display name" wording wrongly implied generic title-aware lookup for living artefacts.
- Consolidate the legacy-target migration set into `legacy_target_migration_error()` in `_common/_markdown.py` so the contract validator in `edit.py` and the structural resolver share one source of truth for `:entire_body` / `:body_preamble` / `:body_before_first_heading` / `:section:...` rejection.
- Remove the dead `find_body_preamble` helper (no callers in `src/`) and migrate its literal-region intro tests onto `resolve_structural_target(body, ":body")["ranges"]["intro"]`.

## v0.32.1 — 2026-04-25

**Fix `brain_edit` MCP consistency for memories and other editable `_Config/` resources, and restore clean test bootstraps.** Editing a memory now refreshes the in-memory router immediately so trigger lookups work in the next call, non-artefact `_Config/` edits no longer leak into the artefact BM25 index, and the repo's local test bootstrap once again installs the YAML dependency required by `config.py`.

- Scope `brain_edit` post-write invalidation by resource in `brain_mcp/_server_artefacts.py`: artefact edits still dirty the router and artefact index, memory edits dirty the router only, and other editable `_Config/` resources no longer queue `_Config/...` paths into the artefact search index.
- Add MCP regressions covering immediate `brain_read(resource="memory")` after a trigger edit and confirming that edited memories do not appear in `brain_search(resource="artefact")`.
- Add `pyyaml>=6.0` to the project/runtime dependency declarations used by local `make install`, so a fresh repo venv can collect and run the MCP server tests without a manual extra install step.

## v0.32.0 — 2026-04-25

**Roll out the breaking `brain_edit` structural contract and stabilise the new target/scope model.** `brain_edit` and `edit.py` now use one explicit `target + selector + scope` contract across artefacts and editable `_Config/` resources, so older target spellings and implicit whole-body mutations are no longer accepted.

- Add `resolve_structural_target(...)` in `_common/_markdown.py` as the shared resolver for body, heading, and callout ranges, then refactor `edit.py`, the MCP server, and the direct CLI onto resolved structural spans and one explicit scope matrix.
- Replace the older reserved-target spellings with the new public contract below, extend the CLI with selector flags (`--scope`, `--occurrence`, `--within`, `--within-occurrence`), and update the docs so artefact `path` is documented as accepting canonical artefact keys, relative paths, and basename/display-name forms.
- Harden the rollout from review findings: selector validation now rejects boolean pseudo-integers, callout body edits fail fast on malformed payloads, targeted frontmatter-only append/prepend no longer claim structural body mutations, the body trailing-newline invariant is restored, heading-body payload validation applies to append/prepend as well as edit, duplicate scope/legacy-target validation collapses to one source of truth, and the orphaned `_surrounding_headings` MCP affordance is removed.
- Tighten the MCP schema so invalid `scope` / `resource` values are rejected at the protocol boundary and add direct + MCP regression coverage for structural resolution, selector ambiguity, trailing-newline preservation, heading-body validation, schema generation, and the CLI parse path.
- Clarify contributor semver policy so breaking Brain contract changes, not just vault-structure changes, use a minor version bump.

| Old public spelling | New public contract |
|---|---|
| `target=":entire_body"` | `target=":body", scope="section"` |
| `target=":body_preamble"` | `target=":body", scope="intro"` |
| `target=":body_before_first_heading"` | `target=":body", scope="intro"` |
| `target=":section:## Heading"` | `target="## Heading", scope="section"` |
| plain body mutation with no target | invalid; supply `target=":body"` plus `scope` |

## v0.31.3 — 2026-04-26

**Canonical upgrade ownership and a consistent Python 3.12 tooling contract.** Upgrade/install/init now present one coherent user-facing lifecycle: `install.sh` is the convenience wrapper, `upgrade.py` owns upgrade behaviour, and `init.py` remains the MCP registration entry point.

- Make `upgrade.py` the explicit upgrade owner. `install.sh` still detects existing vaults, fetches the repo, prompts when needed, and delegates, but it no longer acts like a second owner of upgrade semantics.
- Align the user-facing runtime contract on Python 3.12+ across `install.sh`, `upgrade.py`, `init.py`, and the MCP server path, while still allowing vault scaffolding to proceed when no compatible interpreter is available.
- Move upgrade-time MCP dependency handling into `upgrade.py`. When `.brain-core/brain_mcp/requirements.txt` changes and a vault-local `.venv` exists, the upgrader now best-effort syncs it directly; `--no-sync-deps` remains the explicit opt-out and `install.sh --skip-mcp` passes that through on upgrade.
- Stop the installer wrapper from re-running MCP registration during upgrade mode. Existing project-scoped client config remains in place; upgrade updates the vault and its local runtime without silently broadening side effects.
- Make upgrade follow-up commands caller-independent by printing absolute retry/rebuild commands, so recovery steps work no matter which directory the user ran them from.
- Backfill the vault registry on same-version and skipped installer reruns so `install.sh` still repairs discovery metadata even when no actual upgrade is applied.
- Rewrite the README, getting-started guide, script reference, user tooling reference, and contributor/versioning guidance to reflect the new lifecycle ownership and the pre-1.0 semver policy for install/upgrade contract changes.

## v0.31.2 — 2026-04-25

**Workspace `+Completed` consistency.** Resolve a doc/runtime/migration drift around terminal-status workspaces: the hub file moves to `Workspaces/+Completed/` on completion, but the embedded data folder at `_Workspaces/{slug}/` stays put because it sits outside the artefact taxonomy.

- Clarify the workspace terminal-status convention in `taxonomy.md` (brain-core and template-vault): drop the previous "move embedded data to `_Workspaces/+Completed/{slug}/`" guidance, add an explicit "data folder does not move" note cross-referencing the Data Folder section.
- Update `migrate_to_0_31_0.py` Phase 3 to walk completed workspace hubs (`include_status_folders=True`) so their data folders still get the stem→slug rename. Phase 3's data-folder target remains `_Workspaces/{slug}/` regardless of hub status.
- Fix `workspace_registry._scan_hub_metadata` to walk `Workspaces/` via `iter_markdown_under(..., include_status_folders=True)` so completed hubs are picked up. Previously `list_workspaces()` returned completed embedded workspaces with empty `title`/`status`/`tags`/`hub_path` fields because the metadata scan used a flat `os.listdir` on `Workspaces/`.
- Add a per-hub mtime cache so unchanged hubs aren't re-parsed on every `list_workspaces()` call — completed workspaces accumulate in `+Completed/` over the lifetime of a brain, and the previous behaviour re-read every one on every session refresh. The cache is keyed by absolute path → `(mtime, slug, entry)` with end-of-scan eviction; entries are shallow-copied on emit so callers can't mutate the cached state.

## v0.31.1 — 2026-04-25

**MCP router staleness short-circuit.** Skip the resource-count walk on stable vaults via a stat-based directory-mtime signature, dropping per-TTL cost from ~19ms to ~0.2ms.

- Short-circuit the MCP server's `_check_router_resource_counts` when no resource-holding directory has moved. Previously every router TTL fire (5s) triggered a full walk over the vault's resource dirs and a frontmatter-reading pass across every Living artefact to count index entries (~19ms warm on an 864-artefact vault). Introduce `compile_router.resource_source_dirs(vault_root)` as the single source of truth for which dirs govern `resource_counts` and `count_living_artefact_index_entries` staleness — shallow dirs (vault root, `_Temporal/`, `_Config/Styles/`, `_Config/Memories/`) are stat'd once, tree dirs (`_Config/Skills/`, `.brain-core/skills/`, `_Plugins/`, plus every Living/temporal type folder) are recursively walked stat-only. The MCP server's signature now consumes that helper, so additions at any depth — including new artefact files inside existing Living type folders, which the first pass at this optimisation missed — invalidate the cache. The signature's cache is only written after a successful walk, preventing an error partway through from poisoning later fast-path returns.

## v0.31.0 — 2026-04-24

**Canonical living-artefact keys, stable key identifiers, and parent-backed ownership.** Living artefacts now carry an explicit `key`, ownership is stored canonically in `parent` as `{type}/{key}`, and owner-derived folders replace the old tag-inferred "hub" semantics. The compiled router gains a cached `artefact_index`, create/read/edit/list flows accept canonical artefact keys directly, and upgrade v0.31.0 backfills missing keys and parents without moving files.

- Compile a sibling `artefact_index` keyed by living `{type}/{key}`, reject duplicate living keys, and expose `children_count` for emergent hub detection.
- `brain_create`, `brain_edit`, `brain_read`, `brain_list`, and `convert_artefact` now understand canonical artefact keys; list output adds `key`, `parent`, and `children_count` when present; convert preserves key/parent and fails fast on target-type key collisions.
- Add compliance checks for missing living keys, broken or drifting parent ownership, and status-folder drift; `build_index` now records canonical `key` and `parent` fields.
- Ship `migrate_to_0_31_0.py` as a three-phase upgrade-runner migration: (1) key backfill with `hub-slug` / `hub_slug` promotion and self-tag priority over filename derivation, alongside folder-residency parent and self/owner-tag backfill; (2) child folder relocations via `rename_and_update_links`, backfilling canonical `parent:` frontmatter whenever a move is driven by the single-tag fallback so subsequent `check` runs don't re-warn; (3) workspace data-folder renames plus `.brain/local/workspaces.json` slug remaps. Rollback is handled by the upgrade runner's snapshot context, so the script no longer manages its own backup.
- Harden `workspace_registry._scan_hub_metadata` to index on the canonical frontmatter `key:` when present, falling back to the filename stem, so workspace hubs whose display title diverges from their slug (e.g. `Obsidian Brain.md` / `key: brain`) resolve consistently across registry and embedded data folders.
- Refresh shipped docs, hub/subfolder standards, and template-vault taxonomy/templates/skills to document the new ownership model and owner-derived folder conventions, including releases under `Releases/{owner-folder}/`.
- `install.sh` now pins the clone to `--branch main`, hardening the installer contract against changes to the repo's default branch.
- Shore up the naive-agent path (authoring without tooling): add `.brain-core/standards/keys.md` as the canonical key contract, require `key:` in the four living schemas (projects/people/journals/workspaces), show `key:` in their taxonomy frontmatter examples, and fix the `guide.md` frontmatter example. Introduce a `{{agent: ...}}` template substitution convention — tooling strips these authoring-time hints at create time — and add body hints to the five affected templates so naive agents see the expected frontmatter without placeholder strings leaking into finished artefacts.
- Consolidate the markdown-directory walker stack onto a single canonical primitive: `iter_markdown_under(type_dir, *, include_status_folders=True)` plus a per-artefact wrapper `iter_artefact_paths(vault_root, artefact, ...)` yielding vault-relative paths. Delete `check.py::find_type_files`, `build_index.py::find_md_files`, and `compile_router.py::_find_living_markdown_files`; migrate every caller (compliance checks, router compile, retrieval index, migrations, MCP staleness detection). Behaviour changes: drop `followlinks=True` (no reachable symlinks inside any artefact folder across audited vaults; cycle hazard without a guard); tighten the skip rule from "only `_Archive`" to "any `_*` or `.*`" to match the `is_system_dir` convention. Rename the `iter_artefact_markdown_files` / `iter_living_markdown_files` keyword argument from `include_archived` to `include_status_folders` — the flag gates `+Adopted`/`+Shipped` terminal-status folders, not `_Archive/`.
- Add a streaming frontmatter reader: `read_frontmatter(path) -> dict` in `_common/_frontmatter.py` reads line-by-line until the closing `---` without loading the body into memory, and the complementary `read_artefact(path) -> (fields, body)` covers whole-file reads. Migrate 18 callsites across `check.py` (9), `compile_router.py` (3), `migrate_to_0_31_0.py` (4), `migrate_naming.py` (1), and `workspace_registry.py` (1) to `read_frontmatter`; migrate `build_index.py` (2) and `migrate_to_0_31_0.py` round-trip writes (2) to `read_artefact`. Extract a shared `_parse_yaml_lines` helper so both readers and `parse_frontmatter` share the same YAML-ish line parser.
- Introduce `CheckContext` in `check.py` as a per-run cache that memoizes frontmatter reads and lazy-builds the vault file index, threaded through every compliance check via a keyword-only `ctx=None` parameter so legacy single-check callsites keep working. On a representative vault a full compliance run drops from 4991 to 857 frontmatter reads (≈83% fewer) — one read per unique file instead of one per (file × check that visits it).
- Guard `check_parent_contract` against subfolders whose names aren't valid keys (spaces, mixed case, etc.): route them through the existing orphan branch instead of passing them to `make_artefact_key`, which raised `ValueError: INVALID_KEY` and aborted the whole check.

## v0.30.2 — 2026-04-21

**Keep bootstrap lookup working across the shipped filename-casing transition without forcing user-vault renames.** Vault detection, compliance checks, migrations, and installer checks now tolerate the historical `Agents.md` and `agents.local.md` casings on read via a small enumerated variant list, while all generated styling and canonical write targets stay on `AGENTS.md`, `CLAUDE.md`, and `AGENTS.local.md`.

- Add `_common.find_root_bootstrap_file(...)` plus shared `BOOTSTRAP_VARIANTS`, and route vault-root detection and bootstrap migrations through the canonical-first lookup so case-sensitive filesystems still find older shipped bootstrap filenames.
- Restructure root-allow handling in `check.py` and the template-vault `vault-maintenance` compliance script so canonical and tolerated legacy bootstrap filenames are accepted, while arbitrary user-invented casings remain orphan files.
- Keep `compile_colours.py` styling on canonical filenames only, and align `install.sh` comments/copy-step wording with the new tolerant-read contract.
- Add case-sensitivity-aware regressions for bootstrap lookup, root-file compliance, migration of legacy `Agents.md`, and canonical-only CSS selectors.

## v0.30.1 — 2026-04-21

**Restructure the repo documentation around clear layer indexes, contributor surfaces, and the shipped documentation boundary.** Repo docs now route through layer `README.md` files, contributor-facing docs move under `docs/contributor/`, plugin guidance is split cleanly between vault use and repo authoring, and the documentation set now explicitly conforms to the Agent-Ready Documentation Standard v1.0.0. Shipped `.brain-core/` docs were tightened in parallel: plugin guidance stays vault-facing, `session-core.md` now includes the linking standard in its curated bootstrap list, and the standards set gains a top-level `README.md` plus refreshed naming, shaping, provenance, and extension guidance aligned with current behaviour.

- Repo docs: add `docs/{user,functional,architecture,contributor}/README.md`, add `docs/standards/agent-ready-documentation.md`, move contributor docs into `docs/contributor/`, move the user reference into `docs/user/`, and simplify `docs/README.md`, `docs/CONTRIBUTING.md`, and the root `README.md` so they route instead of duplicating inventories.
- Plugin docs: split the old `docs/plugins.md` into `docs/user/plugins.md` and `docs/contributor/plugins.md`, keep `src/brain-core/plugins.md` self-sufficient for agents that only have shipped docs, and add explicit contributor maintenance guidance linking the repo and shipped plugin surfaces.
- Shipped standards/bootstrap docs: add `src/brain-core/standards/README.md`, include `standards/linking.md` in `src/brain-core/session-core.md`, remove contributor-only repo details from shipped docs, and align shaping/provenance/naming/extending standards with the implemented shaping transcript and naming contracts.
- Convention filenames: normalise `AGENTS.md`, `docs/CONTRIBUTING.md`, and `docs/CHANGELOG.md` to conventional casing, update compatibility checks/migrations, and repoint `CLAUDE.md` symlinks to `AGENTS.md`.

## v0.30.0 — 2026-04-19

**Uniform region-aware markdown scanning via `markdown_region_ranges`.** Every script that scans markdown — wikilink extraction, `rename`, `fix-links`, `check`, and the structural landmark scanners (`collect_headings`, `find_section`, `find_body_preamble`) — now treats fenced code, inline code spans, HTML comments, `$$` math blocks, and raw HTML blocks (`<pre>`/`<script>`/`<style>`) as literal text. A new `replace_wikilinks_in_text(text, pattern, replacement)` helper is the single source of truth for region-aware mutation; both `replace_wikilinks_in_vault` and `fix_links.apply_fixes_to_file` delegate to it, so a documentation example like `` `[[old-name]]` `` is preserved verbatim instead of being silently rewritten. `extract_wikilinks` gains a three-mode `literals="exclude"/"include"/"only"` selector defaulting to `"exclude"`, so callers receive live wikilinks only unless they explicitly opt in. The prior ad-hoc frontmatter skip in `check_wikilinks_in_file` is removed — YAML property wikilinks (`parent: "[[foo]]"`) are real links, matching Obsidian's property-as-link model, so `check` now flags broken property-links it previously hid, and `rename` / `fix-links` rewrite them. Structural scanners pick up the wider region set automatically: heading-shaped lines inside HTML comments, raw HTML, math blocks, or inline code no longer masquerade as landmarks.

- `_common/_wikilinks.py`: add `replace_wikilinks_in_text()` with lazy skip-scan (no region work on no-match files); add `literals=` to `extract_wikilinks()`; drop FM-skip from `check_wikilinks_in_file`; route `replace_wikilinks_in_vault` through the new text helper.
- `_common/_markdown.py`: swap `collect_headings`, `find_section`, `_find_callout_section`, and `find_body_preamble` from `fenced_ranges()` to `markdown_region_ranges()` via shared `literal_ranges()` / `in_any_range()` helpers.
- `fix_links.py`: route `apply_fixes_to_file` through `replace_wikilinks_in_text()` instead of `pattern.subn()`.
- `tests/`: add `test_wikilink_text_replace.py` (text-level mutation + literals modes) and `test_markdown_structural.py` (headings/sections/preamble with non-fence literal regions); extend `test_rename.py`, `test_fix_links.py`, `test_check.py`, and `test_wikilinks_helper.py` with region-aware fixtures and the FM-scan contract.
- `src/brain-core/scripts/README.md`: refresh `_wikilinks.py` and `_markdown.py` descriptions and counts; add `_wikilinks → _markdown` edge.

## v0.29.7 — 2026-04-19

**Align zettelkasten definitions with current standards and catch two docs up with already-shipped behaviour.** Zettelkasten sequencing metadata is renamed from `parent + sequence` to `follows + sequence` so sequence lineage stays distinct from ownership semantics and matches the existing `**Follows:**` body convention. Taxonomy, template, schema, and the artefact-library README are aligned so the frontmatter-relationship-metadata exception is explicit, the template starts schema-valid with the `topic-tag` sentinel seeded instead of blank relationship arrays, and examples use human-readable basenames on a flat `Zettelkasten/` mesh. Architecture and user-guide docs now cover `safe_write_via(...)` alongside `safe_write(...)`, and the queryable `date` naming field used by temporal types such as `temporal/log`.

- Zettelkasten: `schema.yaml`, `taxonomy.md`, `template.md`, and `artefact-library/README.md`.
- Docs catch-up: `docs/architecture/overview.md` and `docs/user/system-guide.md`.

## v0.29.6 — 2026-04-19

**Harden embeddings persistence with the shared atomic write kernel.** `build_index.py` no longer writes `type-embeddings.npy` and `doc-embeddings.npy` via direct `np.save(path, ...)` calls. A new low-level `safe_write_via(...)` primitive now exposes the same sibling-tempfile, `fsync`, and `os.replace()` guarantees for callback-driven serializers, and embeddings consume it through a local `_safe_save_npy(...)` wrapper. `safe_write_json()` now uses the same kernel, so text, JSON, and handle-driven serializers all share one atomic-write path.

- `src/brain-core/scripts/_common/_filesystem.py` now exposes `safe_write_via(...)` for callback-driven serializers and routes `safe_write()` / `safe_write_json()` through the same atomic write kernel.
- `src/brain-core/scripts/build_index.py` now persists both embeddings arrays through a local `_safe_save_npy(...)` helper backed by `safe_write_via(...)`, eliminating the direct path-based `np.save(...)` calls for embeddings outputs.
- Added focused regressions in `tests/test_common_filesystem.py` for `safe_write_via(...)` success, cleanup-on-failure, bounds refusal, and text-mode support, plus a `tests/test_build_index.py` integration test proving `build_embeddings()` routes both `.npy` writes through the new wrapper.
- `docs/architecture/decisions/dd-036-safe-write-pattern.md` and `docs/architecture/security.md` now describe the callback-driven atomic-write kernel and its intended scope.

## v0.29.5 — 2026-04-19

**Phase 1 mutation-safety hardening: startup observability, safe_write alignment, and a non-blocking session-mirror write path.** MCP startup now brackets each major phase with stable begin/success/failure markers for config load, router freshness, index freshness, embeddings load, workspace registry load, and session-mirror refresh. The non-critical session-mirror write is routed through a single long-lived daemon worker that drains a `maxsize=1` queue, so startup is strictly non-blocking (it just enqueues) and rapid-fire refreshes coalesce to the latest intent — no abandoned threads, no late-writer clobber of newer on-disk state. An `atexit` drain (2s cap) lets in-flight writes finish on clean shutdown, and a startup-time sweep removes orphaned `session.md.*.tmp` files left by prior killed workers. `_run_with_timeout` remains in use for the critical router compile and index build phases where fail-loud semantics are correct. The remaining legacy migration write helpers are now aligned with `_common.safe_write`, and `init.py` no longer uses a non-atomic append path when inserting the Claude bootstrap line.

- `src/brain-core/brain_mcp/server.py` now logs explicit startup phases for config load, router freshness, index freshness, embeddings load, workspace registry load, and session-mirror refresh, and runs the session-mirror write on a dedicated `brain-mirror-worker` daemon thread. `_enqueue_mirror_refresh()` is O(1) non-blocking; `_drain_mirror_queue()` is registered via `atexit`; `_sweep_mirror_tmpfiles()` runs early in `startup()`. Mid-session recompile paths (`_ensure_router_fresh()` plus compile/sync/migrate-naming actions) refresh the mirror through the same enqueue helper instead of emitting misleading `startup phase ...` log markers outside startup.
- `docs/architecture/decisions/dd-036-safe-write-pattern.md` documents the worker-queue invariants (no abandoned threads, no late-writer clobber, FIFO last-intent-wins, atexit drain cap, tempfile sweep) and why the pattern is scoped specifically to the mirror path.
- `src/brain-core/scripts/init.py` now routes `ensure_claude_md()` through `_safe_write`, preserving the existing append/idempotency behaviour for populated files while normalising empty existing files to a single bootstrap line.
- `migrate_to_0_16_0.py`, `migrate_to_0_17_0.py`, and `migrate_to_0_27_6.py` now use `_common.safe_write` / `safe_write_json` instead of carrying their own legacy helper copies.
- Added regressions for startup elapsed-time bound under a blocked worker (fresh + stale router branches), coalescing of 20 rapid-fire refreshes to at most three writes, `atexit` drain completion, tempfile sweep (and a negative test that sweep leaves non-matching files alone), explicit mirror-refresh assertions for `brain_action("sync_definitions")` and `brain_action("migrate_naming")` to close the action-site coverage gap, and the `ensure_claude_md()` atomic write path (including empty-file normalisation).
- `docs/contributing-agents.md` now documents `BRAIN_TEST_NON_TMP_ROOT` for running the suite from a `/tmp` worktree.

## v0.29.4 — 2026-04-18

**Fix temporal log date semantics, fail closed on rename collisions, and tighten template-vault maintenance.** `temporal/logs` now keys filenames and month folders from the log's subject day instead of the physical file creation time, so backfilled logs no longer migrate onto the day they were typed. Upgrade-time rename flows now preflight collisions and refuse existing destinations before rewriting links, and post-compile migration failures roll back snapped artefact roots instead of leaving a half-migrated vault behind. The template vault now has an explicit maintenance flow (`make sync-template-check` / `make sync-template`) that refreshes both `_Config/` and `.brain/tracking.json`, and the drift test now enforces true `in_sync` state for installed defaults.

- `src/brain-core/artefact-library/temporal/logs/` now declares `date source date`, adds `date:` to frontmatter, and documents that both the filename and `yyyy-mm/` month folder are keyed by the log's subject day. `template-vault/_Config/Taxonomy/Temporal/logs.md`, `template-vault/_Config/Templates/Temporal/Logs.md`, and the shipped `vault-maintenance` hygiene script were updated to match.
- `src/brain-core/scripts/_common/_reconcile.py`, `_common/_artefacts.py`, `create.py`, and `edit.py` now share one render-time reconciliation path, and temporal folder resolution follows the selected naming rule's `date_source` rather than always assuming `created`.
- `rename.py` now refuses to clobber an existing destination before touching wikilinks. `migrate_naming.py` and `migrations/migrate_to_0_29_0.py` now preflight planned targets and abort on collisions instead of partly applying a conflicting rename plan.
- `upgrade.py` now snapshots post-compile artefact roots as well as the pre-compile `.brain/` / `_Config/` rollback surfaces, so failing post-compile migrations restore both vault content and `.brain-core/` cleanly.
- Added `src/scripts/sync-template-vault.sh`, routed `make sync-template` through it, added `make sync-template-check`, tightened `TestTemplateVaultSync::test_no_drift`, and updated the contributor canary/docs so template-vault tracking drift is caught before commit.

## v0.29.3 — 2026-04-18

**Make pre-compile rollback snapshots binary-safe.** The new `pre_compile_patch` rollback path in v0.29.2 began snapshotting all of `.brain/` and `_Config/` before patch handlers run. That surfaced a real-world vault case where one file under the rollback roots was not valid UTF-8; the snapshot pass raised during upgrade, and the partial rollback could remove later files it had not managed to snapshot yet. Snapshot/restore now operates on raw bytes, so pre-compile rollback covers binary and text files uniformly.

- `src/brain-core/scripts/upgrade.py` now snapshots files in binary mode and restores them through a text-or-bytes-safe atomic write helper. The helper also now uses unique sibling temp files via `tempfile.mkstemp()`.
- Added a regression in `tests/test_upgrade_migrations.py` that reproduces a failing `pre_compile_patch` with a binary file already present under `.brain/local/` and proves rollback restores it intact.

## v0.29.2 — 2026-04-18

**Harden the upgrade migration runner and bundle the v0.29 compatibility patch as a standard migration target.** `upgrade.py` now supports a versioned `pre_compile_patch` stage alongside normal post-compile migrations, so compatibility repairs can be bundled with the migration that needs them instead of being hard-coded into the upgrader. The first use is the v0.29.0 migration bundle, which repairs blocking missing-`date_source` taxonomy definitions before the new compiler gate runs. The runner now treats pre-compile patch failures as fatal rollback conditions, executes migrations in a fresh import context rooted at the upgraded scripts tree, and validates non-default migration targets statically before import.

- `src/brain-core/scripts/upgrade.py` now discovers non-default migration handlers through a strict `TARGET_HANDLERS` contract, records target-aware ledger keys (`VERSION@TARGET`), snapshots vault-side pre-compile patch surfaces for rollback, and aborts the upgrade if a `pre_compile_patch` handler fails.
- `src/brain-core/scripts/migrations/migrate_to_0_29_0.py` now exposes `patch_pre_compile()` as the first standard `pre_compile_patch` example, remediating stale or customised blocking `daily-notes` / `notes` taxonomies narrowly enough to satisfy the v0.29 compile contract before the normal post-compile backfill runs.
- Migration discovery no longer caches parsed ASTs by path, so repeated upgrades in the same Python process always see the current migration file on disk rather than stale handler metadata.
- `src/brain-core/scripts/_common/_naming.py` now keys its compiled regex cache by rule structure rather than `id(rule)`, avoiding incorrect regex reuse when object ids are recycled in long-lived test processes.
- Self-contained write paths in `init.py` and the older migrations `migrate_to_0_16_0.py`, `migrate_to_0_17_0.py`, and `migrate_to_0_27_6.py` now use unique sibling temp files via `tempfile.mkstemp()`, matching the shared atomic-write pattern already used elsewhere.
- Added regression coverage for target-aware migration discovery, fresh package-submodule imports, rollback on failing pre-compile patches, and the v0.29 taxonomy remediation flow in `tests/test_upgrade.py` and `tests/test_upgrade_migrations.py`.

## v0.29.1 — 2026-04-18

**Harden concurrent mutation handling in the MCP server and shared write path.** `safe_write()` now uses a unique sibling tempfile per write instead of a PID-scoped temp name, eliminating same-process tmp-file collisions when two threads target the same file. The MCP wrapper also serializes mutating tool calls inside one server process so vault-wide rewrite paths cannot interleave with parallel `brain_create`, `brain_edit`, `brain_action`, or `brain_process(..., operation="ingest")` calls.

- `src/brain-core/scripts/_common/_filesystem.py` now allocates temp files with `tempfile.mkstemp()` in the destination directory, keeps the same fsync + `os.replace` atomic write pattern, and documents the remaining higher-level last-writer-wins caveat for truly concurrent writers.
- `src/brain-core/brain_mcp/server.py` adds a process-local mutation lock around `brain_create`, `brain_edit`, `brain_action`, and `brain_process("ingest")`, preserving the scripts-as-source-of-truth split while moving concurrency policy into the MCP wrapper.
- Added regression coverage for concurrent same-target `safe_write()` calls and for serialized concurrent `brain_edit` calls in `tests/test_common_filesystem.py` and `tests/test_mcp_server.py`.

## v0.29.0 — 2026-04-18

**Frontmatter-backed filename rendering — filenames are now a pure function of frontmatter.** The v0.28.6 status-aware naming contract restamped temporal artefacts on every edit because `_now` was threaded through the render stack independently of frontmatter. v0.29.0 removes `_now` from the naming engine entirely: every naming rule binds its date tokens to a `date_source` frontmatter field, and rendering reads that field. Creates, edits, renames, and migrations all round-trip through the same reconciled frontmatter state.

- New `_common/_reconcile.py` module: `reconcile_timestamps` (universal `created` / `modified` cascade — frontmatter → filename prefix → mtime → now) and `reconcile_date_source` (type-specific cascade for declared `date_source` fields). Side-effect-free: reads mtime, returns fields. Callers compose the write into their normal path.
- `resolve_naming_pattern` and `render_filename` no longer accept `_now`; date tokens read from `variables[date_source]`. Missing or unparseable `date_source` raises `ValueError`. `resolve_folder` reads `fields["created"]` for temporal `yyyy-mm/` segments — not wallclock.
- Compiler validates `date_source` per rule: temporal classifications default to `created`; living types with date tokens and no declared `date_source` fail compilation with a clear error. `on_status_change` hooks are parsed and carried through the compiled router.
- `brain_edit` now reconciles timestamps before rendering, applies the `{status}_at` convention when `status` changes, honours `on_status_change` overrides (writing uses it for `publisheddate`), and relocates temporal artefacts across `yyyy-mm/` folders when reconciled `created` crosses a month boundary — but only when `created` was authoritative in the edit input, not when it was reconstructed from mtime.
- Taxonomy updates: `living/daily-notes` declares `date_source: date` (dedicated subject-date field, distinct from physical `created`); `living/notes` declares `date_source: created`; `living/writing` gains a draft/published-aware naming rule with `on_status_change: { published: { set: { publisheddate: now } } }`.
- New one-time migration `migrations/migrate_to_0_29_0.py` (auto-run by `upgrade.py` when crossing the v0.29.0 boundary). Walks every artefact; reconciles `created` / `modified`; backfills type-specific `date_source` fields from filename prefixes; infers `living/writing` status from presence of `publisheddate`; relocates temporal artefacts whose reconciled `created` falls in a different month than their current folder; renames files whose selected rule now produces a different name. Runs `migrate_naming.migrate_vault` first (RD-8) so legacy filename formats are canonicalised before backfill cascades from stable prefixes. Idempotent; supports `--dry-run`.
- New `check.py` check `missing_timestamps` (severity `warning`) flags artefacts missing `created` or `modified` in frontmatter — stragglers that arrived outside the create path and haven't been touched by edit yet. Runtime reconcile populates them on next edit.
- Rewrote the date-semantics section of `src/brain-core/standards/naming-conventions.md`: date prefixes are now documented as a synchronised signal derived from a source-of-truth frontmatter field, with the full `date_source` taxonomy, `{status}_at` convention, reconciliation cascade, and one-time migration. Brain Doctor design updated to clarify that structural metadata integrity is in scope (content health remains out) and to register `missing-timestamps` as a future check.

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
