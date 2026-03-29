# Changelog

Follows [semver](https://semver.org/). Changes to vault structure (renamed/removed core files, changed folder conventions) are breaking and bump the minor version.

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
