# Script Reference

Operational reference for scripts in `.brain-core/scripts/`. Each script exposes importable functions and a CLI entry point.

## Script Table

| Script | Purpose | CLI usage |
|---|---|---|
| `compile_router.py` | Compile router from source files and refresh the session mirror | `python3 compile_router.py [--json]` |
| `compile_colours.py` | Generate folder colour CSS | (called by compile_router) |
| `build_index.py` | Build BM25 retrieval index | `python3 build_index.py [--json]` |
| `search_index.py` | BM25 keyword search | `python3 search_index.py "query" [--type T] [--json]` |
| `read.py` | Query compiled router resources | `python3 read.py RESOURCE [--name N]` |
| `create.py` | Create new artefact | `python3 create.py --type T --title "Title" [--body B] [--body-file PATH] [--temp-path [SUFFIX]] [--json]` |
| `edit.py` | Edit artefacts and `_Config/` resources | `python3 edit.py edit\|append\|prepend\|delete_section --path P --body B [--body-file PATH] [--temp-path [SUFFIX]] [--target H] [--json]` |
| `rename.py` | Rename/delete file + update wikilinks (full-path and filename-only), refusing existing-destination collisions | `python3 rename.py "source" "dest" [--json]` |
| `check.py` | Structural compliance checks | `python3 check.py [--json] [--severity S]` |
| `shape_printable.py` | Create printable + render PDF | `python3 shape_printable.py --source P --slug S [--no-render] [--pdf-engine E]` |
| `shape_presentation.py` | Create presentation + render PDF + launch preview | `python3 shape_presentation.py --source P --slug S [--no-render] [--no-preview]` |
| `upgrade.py` | In-place brain-core upgrade with local migration ledger; direct bootstrap writes stay self-contained and atomic | `python3 upgrade.py --source P [--vault V] [--dry-run] [--force] [--sync\|--no-sync] [--sync-deps\|--no-sync-deps] [--json]` |
| `workspace_registry.py` | Workspace slug-path resolution | `python3 workspace_registry.py [--register SLUG PATH] [--unregister SLUG] [--resolve SLUG] [--json]` |
| `migrate_naming.py` | Migrate filenames to generous conventions | `python3 migrate_naming.py [--vault V] [--dry-run] [--json]` |
| `migrations/migrate_to_0_29_0.py` | v0.29.0 migration bundle: `pre_compile_patch` remediates blocking missing-`date_source` taxonomies, then `post_compile` backfills `created`/`modified`/`date_source` across the vault | `python3 migrations/migrate_to_0_29_0.py [--vault V] [--dry-run] [--json]` |
| `migrations/migrate_to_0_31_0.py` | v0.31.0 migration: three-phase upgrade-runner pass that backfills missing living-artefact `key:`/`parent:` fields, relocates child folders to canonical owner-derived paths, and reconciles `_Workspaces/` data folders + `.brain/local/workspaces.json` keys to canonical keys | `python3 migrations/migrate_to_0_31_0.py [--vault V] [--dry-run] [--json]` |
| `fix_links.py` | Auto-repair broken wikilinks | `python3 fix_links.py [--fix] [--json] [--vault V]` |
| `sync_definitions.py` | Install / sync artefact library definitions and classify vault state | `python3 sync_definitions.py [--vault V] [--dry-run] [--force] [--types t1,t2] [--status] [--json]` |
| `config.py` | Vault configuration loader (three-layer merge) | `python3 config.py` |
| `session.py` | Build the canonical session model and refresh `.brain/local/session.md` | `python3 session.py [--json] [--workspace-dir PATH]` |
| `generate_key.py` | Generate operator key + hash for config.yaml | `python3 generate_key.py [--count N]` |
| `process.py` | Content classification, duplicate resolution, ingestion | (library module, used by MCP server) |
| `init.py` | Claude/Codex MCP registration + recorded removal; keeps direct file writes atomic with unique sibling temp files | `python3 init.py [--client {claude,codex,all}] [--user] [--local] [--project PATH] [--remove] [--force]` |

## Architecture

The MCP server is a thin wrapper that imports functions from scripts and holds the compiled router and search index in memory. Scripts are the single implementation — the server adds MCP transport, in-memory caching, process-local mutation serialization for mutating tool calls, and Obsidian CLI delegation. This means:

- Agents without MCP use scripts directly — same logic, same results
- No logic duplication between MCP and CLI paths
- The server gets in-memory caching for free (router/index loaded at startup)
- The server, not the script layer, owns the policy that mutating MCP calls do not overlap in one process
- Standalone scripts pay a cold-start cost reading JSON from disk
- Standalone or multi-process script callers still need to coordinate their own concurrent writes when they target the same files
- New operations are implemented as scripts first, then exposed via MCP

## compile_router.py

*Implemented in v0.5.0.* Transforms human-readable vault config into `.brain/local/compiled-router.json` — a local, gitignored, hash-invalidated cache that all brain-core tools read at runtime. The compiled router is the single interface between configuration and tooling; no script or MCP tool parses taxonomy markdown directly.

**Source files tracked** (changes to any trigger recompilation):

| Source | Purpose |
|---|---|
| `_Config/router.md` | Always-rules and conditional triggers |
| `.brain-core/session-core.md` | Static core bootstrap content; `Always:` block supplies system-level always-rules |
| `.brain-core/VERSION` | Brain-core version |
| `_Config/Taxonomy/Living/*.md` | Living artefact type definitions |
| `_Config/Taxonomy/Temporal/*.md` | Temporal artefact type definitions |
| `_Config/Skills/*.md` | User-defined skills |
| `.brain-core/skills/*.md` | Core system skills |
| `_Config/Styles/*.md` | Style definitions |
| `_Config/Memories/*.md` | Memory definitions |
| `_Config/Plugins/*.md` | Plugin definitions |

**Output structure** (`.brain/local/compiled-router.json`):

| Key | Contents |
|---|---|
| `meta` | brain-core version, compile timestamp, composite SHA-256 hash of all source files |
| `environment` | Platform/runtime detection |
| `always_rules` | Merged rules from session-core.md (system) + router.md (vault) |
| `artefacts` | Discovered types with naming patterns, frontmatter requirements, status enums, terminal statuses, triggers, template paths |
| `artefact_index` | Living-only index keyed by canonical `{type}/{key}`. Each entry records the artefact's path, parent, and `children_count` for emergent-hub detection. Built at compile time from the `key:` and `parent:` frontmatter fields introduced in v0.31.0. |
| `triggers` | Merged conditional triggers from router.md and taxonomy files |
| `skills`, `plugins`, `styles`, `memories` | Discovered enrichment documents |

**Staleness checks:** The MCP server runs two complementary checks on a 5-second TTL. (1) `_check_router` compares the composite `meta.source_hash` (SHA-256 over all tracked source files) to detect edits to existing sources. (2) `_check_router_resource_counts` detects new or deleted resources that aren't in the manifest — but it short-circuits via a stat-only directory-mtime signature (`resource_source_dirs(vault_root)` is the canonical list of dirs that govern resource counts). On stable vaults the signature matches and the resource walk is skipped; cost drops from ~19ms to ~0.2ms. The full walk only fires when a resource directory's mtime advances. See DD-042 for the rationale.

**Importable API** (used by the MCP server to drive its staleness checks): `resource_counts(vault_root)` returns `{key: count}` for the discoverable resource categories. `resource_source_dirs(vault_root)` yields `(rel_path, descend)` pairs identifying every directory whose mtime governs the resource-count answer.

**Post-step generation:** `compile_colours.py` runs as a post-step, reading the compiled router to generate `.obsidian/snippets/brain-folder-colours.css` and `.obsidian/graph.json` colour groups. After a successful compile, `session.py` also refreshes `.brain/local/session.md` so the markdown bootstrap mirror stays aligned with the current canonical session model. Both are called automatically — no separate invocation needed.

**Flags:** `--json` (output JSON to stdout instead of writing to file).

**CLI:**
```bash
python3 compile_router.py           # write .brain/local/compiled-router.json
python3 compile_router.py --json    # output JSON to stdout
```

**Constraints:** Python 3.8+ stdlib only (`_common` imports), self-locating via `find_vault_root()`, deterministic (same inputs → same output, aside from timestamp).

## check.py

*Implemented in v0.9.11.* Router-driven vault compliance checker. Reads the compiled router, validates vault files against structural rules. check.py never parses taxonomy markdown — all per-type rules (naming patterns, required fields, status enums, terminal statuses) come from the compiled router. This means the compiler is the single point that must track vault evolution; check.py adapts automatically when the router is recompiled.

**Flags:** `--json` (structured output), `--actionable` (enriched fix context), `--severity <level>` (filter: `error`/`warning`/`info`), `--vault <path>` (check a specific vault instead of auto-detecting).

**Exit codes:** 0 = clean, 1 = warnings only, 2 = errors present.

**Check catalogue:**

| Check | Severity | Validates |
|---|---|---|
| `root_files` | error | No content files in vault root |
| `naming` | warning | Files match naming pattern from taxonomy |
| `frontmatter_type` | warning | `type` field matches folder-derived type |
| `frontmatter_required` | warning | Required frontmatter fields present |
| `month_folders` | warning | Temporal files in correct `yyyy-mm/` subfolder |
| `archive_metadata` | warning | Files in `_Archive/` (top-level and legacy per-type archives) have `archiveddate` field, `yyyymmdd-` filename prefix, and a terminal status from `frontmatter.terminal_statuses` |
| `status_values` | warning | Status field values match `frontmatter.status_enum` from compiled router |
| `broken_wikilinks` | warning | Wikilink target file does not exist. Scans both body and YAML frontmatter property-links (e.g. `parent: "[[foo]]"`); wikilinks inside fenced/inline code, HTML comments, `$$` math, and raw HTML blocks are treated as literal text and ignored. |
| `ambiguous_wikilinks` | info | Basename-only wikilink matches multiple files. Same region-aware scanning scope as `broken_wikilinks`. |
| `unconfigured_type` | info | Folder has no taxonomy file |
| `missing_timestamps` | warning | Artefact frontmatter missing `created` or `modified` (naming-contract source of truth) |
| `living_key_fields` | error | Living artefact missing a valid canonical `key:` — v0.31.0+ upgrade chain backfills these, so a miss means manual authoring bypassed the tooling |
| `parent_contract` | warning | Child artefact has a broken or drifting `parent:` reference, or sits in a folder that contradicts its declared parent |
| `status_folders` | warning | Terminal-status artefact in the wrong `+{Status}/` subfolder, or a non-terminal artefact stored inside one |
| `taxonomy_type_consistency` | info | Taxonomy `frontmatter_type` equals folder-derived type for a plural key (likely a missing singular in the taxonomy) |
| `router` | error | Compiled router failed to load (missing, invalid JSON, or reported an `error` payload) |

**Constraints:** Python 3.8+ stdlib only, self-locating, stateless, idempotent, stdout-only.

**Relationship with `compliance_check.py`:** The vault-maintenance skill ships a separate `compliance_check.py` for **session hygiene** — quick checks like "did you log today? any transcripts? backups fresh?" It runs after each work block as a sanity check. `check.py` (this design) is **structural compliance** — "do all files have correct frontmatter? naming? month folders?" Deep scan, runs on demand or during maintenance. These are complementary tools, not competing ones.

## install.sh

Top-level script for installing and uninstalling the brain, plus a thin upgrade wrapper for already-installed vaults. Handles four modes depending on what it finds at the target path.

### Usage

```bash
# Fresh install (downloads repo, creates vault, sets up MCP)
bash <(curl -fsSL https://raw.githubusercontent.com/rob-morris/obsidian-brain/main/install.sh)
bash <(curl -fsSL https://raw.githubusercontent.com/rob-morris/obsidian-brain/main/install.sh) /path/to/brain

# From a local clone (skips download)
bash install.sh /path/to/brain

# Upgrade an existing brain vault
bash install.sh /path/to/brain   # detects .brain-core/, offers upgrade

# Install into an existing Obsidian vault or directory
bash install.sh ~/my-vault       # detects non-empty dir, installs brain-core only

# Uninstall
bash install.sh --uninstall /path/to/brain

# Non-interactive (for scripts/agents)
bash install.sh --non-interactive /path/to/brain
bash install.sh --non-interactive --skip-mcp /path/to/brain
bash install.sh --uninstall --non-interactive /path/to/brain
```

### Modes

| Mode | Trigger | What happens |
|---|---|---|
| **Fresh install** | Target is empty or doesn't exist | Copies template vault + brain-core, creates `.venv`, registers project-scope MCP for Claude and Codex (unless skipped or deferred after a dependency failure) |
| **Upgrade** | Target contains `.brain-core/` | Shows installed vs source version, confirms, then delegates to `upgrade.py`; does not own upgrade policy or re-run MCP setup |
| **Existing vault** | Target is non-empty but has no `.brain-core/` | Installs brain-core + config scaffolding only — existing files are never overwritten |
| **Uninstall** | `--uninstall` flag | Removes brain system files; optionally deletes the entire vault |

### Flags

- `--non-interactive` — skip all interactive prompts. On fresh install or existing-vault install: accepts defaults and auto-attempts MCP setup unless you also pass `--skip-mcp`. On uninstall: removes system files without prompting and skips the vault-deletion offer entirely. Also bypasses the stdin pipe detection error. On upgrade, it auto-confirms the handoff to `upgrade.py`. `install.sh` does not expose upgrade override semantics; use `upgrade.py --force` directly for same-version re-apply, downgrade, or migration rerun flows.
- `--skip-mcp` / `--no-mcp` — skip `.venv` creation, dependency installation, and MCP registration on install flows. Useful for network-restricted or vault-only installs. On upgrade flows it passes `--no-sync-deps` through to `upgrade.py`, so the canonical upgrader still owns the behavior while the wrapper preserves the opt-out.
- `--uninstall` — enter uninstall mode. Must be the first argument.
- **Path** (positional, optional) — target directory. Defaults to current directory (prompted interactively, or `pwd` with `--non-interactive`).

### Requirements

- **git** — required (for cloning the repo when not running from a local clone). The installer pins `--branch main` explicitly, so the installer contract is independent of the repo's default-branch setting.
- **python3** — required (any version, for basic preflight)
- **Python 3.12+** — recommended for all user-facing entry points (`install.sh`, `init.py`, `upgrade.py`, MCP server runtime). The installer searches for `python3.13`, `python3.12`, then `python3`. If no 3.12+ interpreter is found, the vault can still be scaffolded, but upgrade handoff plus `.venv` / MCP setup are skipped. The script prints guidance for installing Python later and running `init.py` manually.
- **Package index access for MCP setup** — fresh installs and existing-vault installs may install `.brain-core/brain_mcp/requirements.txt` into the vault-local `.venv`. If that step fails, the installer keeps the vault intact, skips MCP registration, and prints manual retry commands instead of aborting the whole run. Upgrade-specific dependency guidance comes from `upgrade.py`.

### Safety guards

The script refuses dangerous target paths:

- System directories: `/`, `/usr`, `/usr/*`, `/bin`, `/sbin`, `/etc`, `/var`, `/tmp`, `/System`, `/Library`
- Home directory directly (`$HOME`) — must be a subdirectory
- The source repo itself (when running from a clone)
- Any path whose parent directory doesn't exist

**Stdin pipe detection:** running `curl ... | bash` breaks interactive prompts. The script detects this and exits with a message showing the correct `bash <(curl ...)` invocation. Pass `--non-interactive` to skip all prompts and bypass the check.

### Existing vault behaviour

When the target is a non-empty directory without `.brain-core/`, the script installs brain-core alongside existing files:

- **Always installed:** `.brain-core/` (the brain engine)
- **Created if missing:** `_Config/`, `_Assets/`, `_Temporal/`, `_Plugins/`, `_Workspaces/`, `.backups/`
- **Copied if absent:** `AGENTS.md`, `CLAUDE.md`
- **Always overwritten:** `.obsidian/snippets/brain-folder-colours.css` (namespaced, safe — this file is ours)

Existing files and directories are never touched. The script detects `.obsidian/` and adjusts its messaging accordingly ("Existing Obsidian vault detected" vs "Existing directory detected").

### Uninstall

Two-stage confirmation protects against accidental data loss (interactive mode only — `--non-interactive` skips both stages):

1. **System files** — confirms removal of `.brain-core/`, `.brain/`, `.venv/`, `CLAUDE.md`, plus recorded Brain-managed project MCP entries from `.mcp.json` / `.codex/config.toml`. Your notes and vault structure are untouched. With `--non-interactive`, this stage is skipped (auto-confirmed) and the script exits after removal.
2. **Full vault deletion** — optionally offers to delete the entire directory. Counts and displays the number of artefacts that would be lost. Requires typing `"farewell, cruel world"` to confirm. Not available with `--non-interactive`.

User-scope cleanup remains explicit. The uninstall flow reminds you to run `init.py --remove --user` before deleting the vault when that scope is in use.

## init.py

Setup script at `.brain-core/scripts/init.py`. Configures Claude Code and Codex to use the brain MCP server. Self-contained (no `_common` imports), idempotent, and explicit about both client and scope:

- **Client selector** — `--client claude|codex|all` (default: `all`)
- **Project** (default): Claude writes `.mcp.json`; Codex writes `.codex/config.toml`; Claude also ensures `CLAUDE.md` and a SessionStart hook in `.claude/settings.local.json`
- **Local** (`--local`): Claude only. Writes `.claude/settings.local.json` + `.claude/CLAUDE.local.md` in the target directory
- **User** (`--user`): Claude writes `~/.claude.json`; Codex writes `~/.codex/config.toml`

`.mcp.json` and `.codex/config.toml` contain machine-local absolute paths (`command`, `BRAIN_VAULT_ROOT`, `PYTHONPATH`, `BRAIN_WORKSPACE_DIR`). Whether to commit these files is the vault or workspace's choice — Brain does not mandate. See [DD-040](../architecture/decisions/dd-040-workspace-architecture.md) for the scoping contract.

When multiple scopes are configured, project takes priority over local, which takes priority over user once the project-scoped client entry is active. For Claude, that means the project's `.mcp.json` entry has been approved via `/mcp`. For Codex, that means the project is trusted and the project-scoped MCP is enabled. The script warns if a matching user-scope registration already exists when installing at project or local scope.

Codex has no native local scope. `--client codex --local` exits with an error. `--client all --local` applies Claude local setup, skips Codex local setup, prints a warning, and exits success.

Optional: `--vault <path>` overrides vault root auto-detection (used by `install.sh` when the script isn't inside the vault).

Registration strategy:

- **Claude** — prefers `claude mcp add-json` when CLI available, falls back to direct JSON file editing
- **Codex** — direct TOML file editing of native Codex config surfaces
- **Removal** — `--remove` deletes only recorded Brain-managed entries from the requested client/scope; user-scope cleanup is explicit-only; project uninstall uses the same recorded removal path

Claude project-scope installs still require Claude Code's own trust step for `.mcp.json`. `init.py` does not auto-approve that trust boundary. After a project install, open Claude Code in the target directory and run `/mcp` to approve `brain` if prompted. This matters most when `~/.claude.json` already contains a user-scoped `brain`: until the project entry is approved, Claude may route `mcp__brain__*` calls to the user-scoped server instead. `claude mcp list` is only a health check; it does not prove project approval.

Codex project-scope installs have the same activation caveat in different words: the project-scoped `.codex/config.toml` entry outranks the user-scoped one once the project is trusted and `brain` is enabled for that project. Until then, Codex may keep using the user-scoped `brain` from `~/.codex/config.toml`. `codex mcp list` is a health check, not proof that the project-scoped server is the one serving calls; verify by calling `brain_session` and confirming `environment.vault_root`.

All writes are atomic (tmp + fsync + rename). `init.py` records client, scope, config path, target path, and server config in `.brain/local/init-state.json` so later removal can compare the current entry against recorded ownership instead of guessing from file presence.

For folder-scoped installs, `init.py` also scaffolds `.brain/local/workspace.yaml` when absent (except when targeting the vault root itself). If a legacy manifest exists at `.brain/workspace.yaml`, it is migrated to the new location automatically. The scaffold is intentionally minimal: it gives the workspace a stable slug and a default `workspace/{slug}` tag, but leaves richer metadata and links for humans or agents to evolve later.

**Dependencies:** Python 3.8+ stdlib only. Detects a Python with `mcp` package for the server config (vault `.venv` -> current Python -> PATH search).

## session.py

Canonical session bootstrap builder at `.brain-core/scripts/session.py`. Owns one session model with two renderers:

- **JSON** — used by `brain_session`
- **Markdown** — written to `.brain/local/session.md` for the no-MCP bootstrap path

**Inputs:** `.brain-core/session-core.md`, `.brain/local/compiled-router.json`, `_Config/User/preferences-always.md`, `_Config/User/gotchas.md`, merged config (when available), runtime environment state, and active profile when known.

**Behaviour:**
- Builds the canonical session model from static core bootstrap content, structured core-doc references, and dynamic vault state
- Writes `.brain/local/session.md` on direct CLI execution
- Is also called by the MCP server and router compile path so JSON and markdown stay in parity for shared content. MCP-side invocations are dispatched through a dedicated daemon worker with a `maxsize=1` coalescing queue (see dd-036), so callers never block on the markdown write and rapid successive refreshes collapse to the latest intent
- When an active workspace is supplied, reads `.brain/local/workspace.yaml` from that workspace (falling back to the legacy `.brain/workspace.yaml` with a warning) and exposes raw `workspace` identity plus any resolvable `workspace_record` and `workspace_defaults`
- Keeps the current `context` parameter as a forward-compatible stub; no context-specific scoping yet

**CLI:**
```bash
python3 session.py                # compact JSON to stdout + refresh session mirror
python3 session.py --json         # pretty JSON to stdout + refresh session mirror
python3 session.py --context slug # include context stub in the JSON output
```

## upgrade.py

Canonical upgrade script at `src/brain-core/scripts/upgrade.py`. It is shipped inside `.brain-core/scripts/` for vault-local use, but the documented upgrade entry point is still "run it from a clone of this repo with an explicit `--source`". Copies a source brain-core directory into a vault's `.brain-core/`, removing obsolete files, with version awareness. Self-contained (no `_common` imports) because it replaces `_common/` during execution.

**Design decisions:**
- Source path is required (`--source`), not auto-detected — explicit is safer for an operation that overwrites system files
- **Backup + pre-compile patch target + compile validation + rollback** — before modifying anything, `.brain-core/` is backed up to `/tmp/`. After copying files, any versioned `pre_compile_patch` handlers run first so narrowly-scoped compatibility repairs can unblock the new compiler. `compile_router.py` then runs as the validation gate. If copy, patching, compile, or a later post-compile migration fails, both the vault snapshots and `.brain-core/` are restored. The pre-compile rollback snapshots `.brain/` and `_Config/` in raw bytes, so binary or non-UTF-8 files under those roots no longer break rollback; post-compile migrations also snapshot affected artefact roots so half-applied renames do not survive a failed migration. Result is logged to `.brain/local/last-upgrade.json` for diagnostics
- **Per-migration ledger** — each migration recorded as successful or skipped is written to `.brain/local/migrations.json`, and a coarse `.brain/local/.migrated-version` fast-path marker is refreshed once all `post_compile` migrations up to the installed version are accounted for. Target-specific entries are keyed as `VERSION@TARGET` (for example `0.29.0@pre_compile_patch`). This prevents historical migrations from replaying just because `.brain-core/` was deleted and reinstalled. `--force` bypasses the ledger and re-runs migrations up to the target version
- **Standard migration targets** — `post_compile` is the default versioned migration stage; `pre_compile_patch` is the standard patch stage for compatibility fixes that must land before the compile gate. Migrations declare non-default targets via `TARGET_HANDLERS`
- **Dependency management** — MCP server dependencies are declared in `.brain-core/brain_mcp/requirements.txt`. When this file changes during an upgrade, the CLI best-effort syncs the vault-local `.venv` itself if one exists. `--no-sync-deps` skips that step and prints the exact absolute retry command instead. The `install.sh` wrapper delegates this behavior to `upgrade.py`, and `install.sh --skip-mcp` passes through the opt-out.
- **Not exposed via MCP** — self-upgrading MCP servers are an anti-pattern (a prompt-injected agent could point upgrade at a crafted directory). The MCP server detects version drift and exits cleanly; the client restarts it with the new code
- **Post-upgrade definition sync** — after a successful upgrade, `sync_definitions` runs automatically. Safe updates (upstream changed, no local changes) always apply. Conflicts (both upstream and local changed) are returned as warnings for the caller to present. If `artefact_sync` is `"skip"` in `.brain/preferences.json`, no sync runs. CLI flags `--sync` / `--no-sync` override the preference. Sync failures are captured in the result — they never crash the upgrade

**CLI:**
```bash
python3 upgrade.py --source /path/to/src/brain-core              # upgrade
python3 upgrade.py --source src/brain-core --vault /path --dry-run  # preview
python3 upgrade.py --source src/brain-core --force                  # re-apply or downgrade
python3 upgrade.py --source src/brain-core --json                   # structured output
python3 upgrade.py --source src/brain-core --sync      # upgrade + sync definitions
python3 upgrade.py --source src/brain-core --no-sync    # upgrade without sync
python3 upgrade.py --source src/brain-core --no-sync-deps  # skip upgrade-time MCP dep sync
```

## sync_definitions.py

The single tool for reconciling vault `_Config/` definitions with the artefact library. Covers install of new library types, update of drifted ones, conflict surfacing, and a read-only status classifier.

**Invocation modes:**

| Command | Behaviour |
|---|---|
| `sync_definitions.py` | Safely syncs already-installed types. Never installs new ones. |
| `sync_definitions.py --types living/X` | Installs X if absent, updates if safely updatable. No `--force` needed to install — install is additive. |
| `sync_definitions.py --types X --force` | Overwrites local customisation or conflict for X with the library version. |
| `sync_definitions.py --force` | Overwrites conflicts for already-installed types (does not install new types). |
| `sync_definitions.py --status` | Read-only. Classifies every library type by vault state. |
| `sync_definitions.py --dry-run` | Preview any of the above without modifying files. |

**Design decisions:**
- **Install requires explicit intent.** Bare sync never installs uninstalled types — that's the safety rail keeping upgrade.py from surprise-installing every new library type. `--types X` is the explicit install path.
- **Install is not destructive.** It's additive (file doesn't exist; library file is copied in). `--force` is only needed when there's something to overwrite.
- **Status is a separate mode**, not the default. Bare invocation stays a sync so chained callers (`upgrade.py`) keep working.
- **Target-missing is indistinguishable from upstream-added.** If tracking says a file is installed but it's gone, sync just copies it back from the library — same code path as a genuine upstream addition.

**State taxonomy** (used by `--status` and by callers of `status_definitions()`):

| State | Meaning | Sync action |
|---|---|---|
| `uninstalled` | Not present in the vault at all | Install via `--types X` |
| `in_sync` | Hashes match library | None |
| `sync_ready` | Library has content the vault lacks or differs on (upstream side) | Bare sync auto-applies |
| `locally_customised` | Local diverged, library unchanged | Preserved; `--force` to revert |
| `conflict` | Both sides diverged | Warned; `--force` to overwrite |

Plus a separate `not_installable` bucket in `--status` output for library-side errors (missing manifest sources, etc.) — these cannot be fixed from the vault.

**MCP parity:** exposed via `brain_action("sync_definitions", params={...})`. Pass `status=true` for the read-only classifier. All CLI params (`types`, `force`, `dry_run`) are accepted on the MCP surface too.

## build_index.py / search_index.py

BM25 keyword search over all vault markdown files. Built offline like the compiled router, queried at search time from a pre-built index.

**Key properties:**
- **Zero dependencies** — hand-rolled BM25, Python 3.8+ stdlib only, matching `compile_router.py` constraints
- **Same folder discovery** — reuses `scan_living_types()` / `scan_temporal_types()` patterns, then recurses each type folder for `.md` files
- **Whole-document indexing** — each note is one entry (sufficient at vault scale)
- **Build/search split** — `build_index.py` builds the index, `search_index.py` queries it. Separate scripts, no cross-imports.

**BM25 parameters:** `k1=1.5`, `b=0.75`. IDF: `log((N - df + 0.5) / (df + 0.5) + 1)`. Score: `sum IDF(t) * (tf(t,d) * (k1+1)) / (tf(t,d) + k1 * (1 - b + b * dl/avgdl))`. **Title boosting** (v0.11.3): terms appearing in the document title receive an additional `IDF(t) * 3.0` score contribution, stored as `title_tf` per document. Backward compatible — older indexes without `title_tf` still work.

**Output:** `.brain/local/retrieval-index.json` — local, gitignored. Contains corpus stats (document frequencies, average document length), per-document term frequencies, and metadata (path, title, type, tags, status, modified).

**CLI:**
```bash
python3 build_index.py           # write .brain/local/retrieval-index.json
python3 build_index.py --json    # output JSON to stdout
python3 search_index.py "query"  # search and print ranked results
python3 search_index.py "query" --type living/design --tag brain-core --top-k 5
python3 search_index.py "query" --json  # structured output
```

**Tokeniser:** lowercase, split on non-alphanumeric, strip tokens < 2 chars.

**Snippets:** ~200 chars centred on first query term match in body, expanded to nearest word boundary. Falls back to first 200 chars if no match.

**Build trigger:**

- **MCP server** (implemented v0.7.0) — rebuilds on startup if stale (compares index timestamp against file mtimes), exposes `build_index` as a `brain_action` for mid-session refresh. Same pattern as compiled router auto-compile. Incremental updates (v0.15.10): `brain_create`/`brain_edit` queue single-file upserts via `index_update()` instead of triggering full rebuilds; destructive actions (rename/delete/convert) set a dirty flag for full rebuild on next search. Filesystem staleness checks for both router and index are throttled by a 5-second TTL to reduce per-call I/O.
- **Obsidian plugin** (planned) — watch for `.md` file changes and trigger rebuild so the index stays fresh for non-agent use cases (in-Obsidian search UI, etc.). Vault files can be modified by agents, by Obsidian, or directly via the filesystem — all three paths need to result in a fresh index.
- **Manual** — `python3 build_index.py` from the vault root.

## Taxonomy Discovery

**Taxonomy discovery**: agents discover artefact types via `_Config/Taxonomy/`. The filesystem is the index (DD-018) — no separate readme or enumeration file is needed.

**Key derivation convention**: type key = lowercase folder name, spaces to hyphens. e.g. `Daily Notes` -> `daily-notes` -> `_Config/Taxonomy/{classification}/daily-notes.md`. No manual registry needed. All MCP tools that accept a type parameter tolerate singular forms — `"report"` resolves to `"reports"`, `"idea"` to `"ideas"` — via normalised matching in `_common.match_artefact()`.

## Cross-references

- **Co-located module map:** [`src/brain-core/scripts/README.md`](../../src/brain-core/scripts/README.md) — function-level reference for each script module
- **Design decisions:** [`../architecture/decisions/`](../architecture/decisions/) — DD-009 (router-driven checks), DD-018 (no taxonomy index), DD-019 (succinct readme), DD-023 (init script / script architecture)
- **MCP tool specs:** [`mcp-tools.md`](mcp-tools.md) — the MCP server that wraps these scripts
