# Changelog

Follows [semver](https://semver.org/). Changes to vault structure (renamed/removed core files, changed folder conventions) are breaking and bump the minor version.

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
