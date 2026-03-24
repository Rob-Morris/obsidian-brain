# Changelog

Follows [semver](https://semver.org/). Changes to vault structure (renamed/removed core files, changed folder conventions) are breaking and bump the minor version.

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
