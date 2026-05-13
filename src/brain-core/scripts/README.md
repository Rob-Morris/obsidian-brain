# scripts/

Scripts are the **source of truth** for all vault operations. The MCP server (`brain_mcp/server.py`) is a thin wrapper that imports functions from these scripts and holds the compiled router and search index in memory. Scripts are the single implementation — the server adds MCP transport, in-memory caching, process-local mutation serialization for mutating tool calls, and Obsidian CLI delegation. Agents without MCP use scripts directly and get identical results. New operations are implemented as scripts first, then exposed via MCP.

Semantic and hybrid retrieval remain optional. Enable them from an installed
vault with `python3 .brain-core/scripts/configure.py semantic --enable`
before using embedding-backed search or evaluation flows. That command writes
the local semantic-retrieval flag, installs the pinned semantic Python stack,
snapshots the pinned model under `.brain/local/semantic-models/`, records
`.brain/local/semantic-model-manifest.json`, and refreshes embeddings sidecars.
After provisioning, ordinary search/process/index paths load the local snapshot
with no surprise Hugging Face fetches. Missing sidecars still degrade cleanly;
present-but-corrupt sidecars now fail explicitly at search/evaluation entry
points so the owning boundary can rebuild or repair them deliberately. The
pinned stack targets current upstream wheel-supported platforms; Intel macOS
remains lexical-only.

## Module Table

| Script | Purpose | CLI usage |
|---|---|---|
| `_common/` | Shared utilities package: vault discovery, frontmatter parsing, serialisation, CLI parser helpers, BM25 tokenisation | (library only) |
| `_lifecycle_common.py` | Launcher-safe lifecycle helpers shared by configure/repair: managed-runtime bootstrap, probe helpers, result envelopes, and human/json rendering | (library only) |
| `_repair_common.py` | Launcher-safe repair metadata, scope definitions, and exact command builders | (library only) |
| `_repair_runtime.py` | Managed-runtime repair scope implementations plus additive compliance repair detectors | (library only) |
| `_semantic/` | Internal semantic package: config flags, model/runtime provisioning, embeddings helpers, and vector-ranking/runtime utilities shared by build/search/configure/repair flows | (library only) |
| `build_index.py` | Build retrieval index and refresh embeddings sidecars from the provisioned local semantic model when `semantic_processing` or `semantic_retrieval` is enabled and router data is available | `python3 build_index.py [--json]` |
| `construct_benchmark_fixture.py` | Derive a vault-native retrieval benchmark fixture plus audit JSON from an existing vault, including semantic-variant audit diagnostics and optional externally seeded semantic or hybrid candidates | `python3 construct_benchmark_fixture.py --fixture-out PATH [--audit-out PATH] [--semantic-strategy S] [--semantic-seed-file PATH] [--hybrid-seed-file PATH] [--json]` |
| `evaluate_search.py` | Benchmark lexical, semantic, and hybrid retrieval against a JSON query set | `python3 evaluate_search.py --benchmark PATH [--mode M]... [--json]` |
| `check.py` | Router-driven structural compliance checks; human output now prints exact `repair.py` commands for repairable router/MCP/local-registry/semantic drift and structured results include `repair` metadata | `python3 check.py [--json] [--actionable] [--severity S] [--vault V]` |
| `configure.py` | Explicit installed-vault lifecycle entry point for semantic-retrieval opt-in and runtime/model provisioning | `python3 configure.py semantic --enable [--no-provision] [--json] [--vault V]` |
| `compile_colours.py` | Generate folder colour CSS | (called by compile_router) |
| `compile_router.py` | Compile router from source files and refresh session markdown | `python3 compile_router.py [--json]` |
| `config.py` | Vault configuration loader (three-layer merge) | `python3 config.py` |
| `create.py` | Create new artefact or `_Config/` resource | `python3 create.py --type T --title "Title" [--body B] [--body-file PATH] [--temp-path [SUFFIX]] [--json]` |
| `edit.py` | Edit artefacts via CLI with explicit `target + selector + scope`; the importable helpers also back `brain_edit` for editable `_Config/` resources | `python3 edit.py edit\|append\|prepend\|delete_section --path P [--body B\|--body-file PATH] [--frontmatter JSON] [--target T] [--scope S] [--occurrence N] [--within T --within-occurrence N]... [--json]` |
| `fix_links.py` | Auto-repair broken wikilinks | `python3 fix_links.py [--fix] [--json] [--vault V]` |
| `generate_key.py` | Generate operator key + hash for config.yaml | `python3 generate_key.py [--count N]` |
| `init.py` | Claude/Codex MCP server registration + recorded removal; requires a Python 3.12+ runtime with the `mcp` package, scaffolds `.brain/local/workspace.yaml` for folder-scoped installs (migrates legacy `.brain/workspace.yaml` automatically), and writes config atomically with unique sibling temp files. Project-scoped MCP still needs client-side activation before it outranks user scope: approve via `/mcp` in Claude, or trust/enable the project-scoped server in Codex. | `python3 init.py [--client {claude,codex,all}] [--user] [--local] [--project PATH] [--remove] [--force]` |
| `list_artefacts.py` | Enumerate vault artefacts and resources (unranked, no cap) | (library module, used by MCP server) |
| `migrate_naming.py` | Migrate filenames to generous naming conventions | `python3 migrate_naming.py [--vault V] [--dry-run] [--json]` |
| `obsidian_cli.py` | IPC client for native Obsidian CLI | (library module, used by MCP server) |
| `process.py` | Experimental content classification, duplicate resolution, ingestion | (library module, used by MCP server) |
| `repair.py` | Explicit infrastructure repair entry point; bootstraps from a compatible Python 3.12+ launcher, converges into the central managed runtime at `~/.brain/venvs/py<X.Y>-<sha16>/`, then runs one named repair scope. `mcp` repairs installed current-vault project MCP state only; `semantic` repairs the pinned semantic runtime, local model snapshot/manifest, and sidecars after a vault has opted in. | `python3 repair.py {mcp,router,lexical,registry,semantic} [--vault V] [--dry-run] [--json]` |
| `read.py` | Query compiled router resources | `python3 read.py RESOURCE [--name N]` |
| `rename.py` | Rename/delete file + update wikilinks, refusing existing-destination collisions | `python3 rename.py "source" "dest" [--json]` |
| `search_index.py` | Lexical, semantic, or hybrid local search with exact-anchor lexical wins, strong semantic champions, Brain-only title champions, and semantic-rescue fusion for disjoint leaders | `python3 search_index.py "query" [--type T] [--mode M] [--json]` |
| `session.py` | Build the canonical session model and refresh `.brain/local/session.md` | `python3 session.py [--json] [--workspace-dir PATH]` |
| `shape_printable.py` | Create printable + render PDF | `python3 shape_printable.py --source P --slug S [--no-render] [--pdf-engine E]` |
| `shape_presentation.py` | Create presentation + render PDF + launch Marp preview | `python3 shape_presentation.py --source P --slug S [--no-render] [--no-preview]` |
| `start_shaping.py` | Bootstrap a shaping session for an existing artefact | `python3 start_shaping.py --target P [--title T] [--vault V]` |
| `sync_definitions.py` | Sync artefact library definitions to vault `_Config/`, using raw tracked hashes plus markdown-aware comparison for `.md` files so harmless table-padding rewrites do not surface as drift | `python3 sync_definitions.py [--vault V] [--dry-run] [--force] [--types t1,t2] [--status] [--json]` |
| `upgrade.py` | Canonical brain-core upgrade entry point with pre-compile compatibility patches, a target-aware local migration ledger, binary-safe rollback snapshots, running stage snapshots in `.brain/local/last-upgrade.json`, self-contained atomic writes, and best-effort central-runtime provisioning when `requirements.txt` changes | `python3 upgrade.py --source P [--vault V] [--dry-run] [--force] [--sync\|--no-sync] [--sync-deps\|--no-sync-deps] [--json]` |
| `vault_registry.py` | User-home registry of installed brain vaults (`$XDG_CONFIG_HOME/brain/vaults`, default `~/.config/brain/vaults`) | `python3 vault_registry.py [--register PATH\|--backfill PATH\|--unregister PATH\|--list [--json]\|--prune\|--resolve ALIAS]` |
| `workspace_registry.py` | Workspace slug→path resolution | `python3 workspace_registry.py [--register SLUG PATH] [--unregister SLUG] [--resolve SLUG] [--json]` |

## Bounded Context Map

The script layer is organised into 8 bounded contexts. This is an architectural ownership map, not a packaging requirement.

| Context | Scripts |
|---|---|
| Compilation | `compile_router.py`, `compile_colours.py`, `build_index.py`, `sync_definitions.py` |
| Artefact Operations | `create.py`, `edit.py`, `read.py`, `rename.py`, `fix_links.py`, `start_shaping.py`, `shape_printable.py`, `shape_presentation.py` |
| Compliance | `check.py` |
| Content Intelligence | `search_index.py`, `evaluate_search.py`, `construct_benchmark_fixture.py`, `list_artefacts.py` |
| Session & Configuration | `session.py`, `config.py`, `workspace_registry.py`, `generate_key.py` |
| Lifecycle Management | `init.py`, `repair.py`, `upgrade.py`, `vault_registry.py`, `migrate_naming.py`, `migrations/` |
| MCP Integration | `brain_mcp/server.py`, `brain_mcp/proxy.py` |
| Platform Integration | `obsidian_cli.py` |

Import policy:
- Depend on `_common/` public API, not another context's private helpers.
- Prefer top-level script functions as cross-context seams.
- Keep MCP concerns in `brain_mcp/`; keep platform adapters as leaves.

## Dependency Graph

### Import `_common`

These scripts import from `_common/` for vault discovery, frontmatter parsing, and shared utilities:

- `build_index.py`
- `construct_benchmark_fixture.py`
- `check.py`
- `compile_colours.py`
- `compile_router.py`
- `config.py`
- `create.py`
- `edit.py`
- `evaluate_search.py`
- `fix_links.py`
- `list_artefacts.py`
- `migrate_naming.py`
- `read.py`
- `_repair_runtime.py`
- `rename.py`
- `search_index.py`
- `session.py`
- `shape_printable.py`
- `shape_presentation.py`
- `start_shaping.py`
- `sync_definitions.py`
- `workspace_registry.py`

### Standalone (no `_common` dependency)

- `generate_key.py` — stdlib only
- `init.py` — stdlib only; self-contained because it may run before the managed runtime is available
- `obsidian_cli.py` — stdlib only; IPC socket client
- `_repair_common.py` — stdlib only; shared repair metadata and command builders
- `repair.py` — bootstrap-safe launcher that repairs or creates the central managed runtime before handing off into it
- `upgrade.py` — deliberately self-contained (it may replace `_common` during execution); duplicates only `find_vault_root()`, runs versioned `pre_compile_patch` handlers before compile validation, snapshots `.brain/` and `_Config/` for rollback using raw-byte restore so binary local-state files are safe, snapshots post-compile artefact roots before running migrations, records target-aware migration history in `.brain/local/` so reinstalls do not replay migrations unless forced, writes running stage snapshots to `.brain/local/last-upgrade.json` before long follow-up phases, and prints caller-independent follow-up commands after upgrade-time dependency handling

## `_common/` Package Structure

The `_common/` package decomposes shared utilities into focused modules. `__init__.py` re-exports all public names, so consumers continue to `from _common import …` unchanged.

Boundary rule:
- `__init__.py` exports only supported public API.
- Underscore-prefixed helpers stay module-internal by default.
- If another script genuinely needs a helper across module boundaries, promote it to a public name rather than importing a private helper through the facade.

| Module | Purpose | Functions |
|--------|---------|-----------|
| `_vault.py` | Vault root discovery, version, scanning, artefact matching | 8 |
| `_artefacts.py` | Shared artefact naming, folder resolution, config-resource paths, file reads, frontmatter date parsing | 6 |
| `_naming.py` | Naming-rule selection, filename render/validate, title reverse-parse | 6 |
| `_reconcile.py` | §5 reconciliation cascade for `created`/`modified` and type-specific `date_source` fields | 2 |
| `_router.py` | Compiled router loading, naming-pattern matching, artefact path validation | 6 |
| `_filesystem.py` | Safe writes, bounds checking, body file resolution | 7 |
| `_frontmatter.py` | Frontmatter parsing, serialisation, streaming `read_frontmatter`, and whole-file `read_artefact` | 4 |
| `_wikilinks.py` | Wikilink extraction, file index, broken link resolution, region-aware text mutation | 15 |
| `_markdown.py` | Heading/callout parsing, shared structural target resolution, and typed literal-text regions (fenced code, inline code, HTML comments, `$$` math, raw HTML) | 28 |
| `_slugs.py` | Slug generation, validation, title/filename/slug conversions | 9 |
| `_search.py` | BM25 tokenisation and exact-anchor query detection | 1 |
| `_templates.py` | Timestamp utilities, template variable substitution | 3 |
| `_coerce.py` | Type coercion helpers for MCP boundary | 1 |

Internal dependencies flow from leaves to integrators:

```
_slugs, _search, _markdown, _frontmatter, _templates, _vault  (standalone)
_artefacts   → _slugs, _vault
_naming      → _artefacts, _slugs
_reconcile   → _artefacts
_router      → _wikilinks
_filesystem  → _vault
_wikilinks   → _vault, _filesystem, _slugs, _markdown
```

Tests may import owning submodules directly when validating internal helpers.

## Shared Patterns

**`--json` flag** — All CLI-facing scripts that produce output accept `--json` to emit a machine-readable JSON result instead of human-readable text. Library-only modules do not expose this flag.

**`--vault` and `find_vault_root()`** — Vault location is auto-detected via `_common.find_vault_root()`, which walks up from the current directory looking for `.brain/`. Scripts accept `--vault` to override this. The MCP server passes the vault path explicitly; standalone CLI invocations rely on auto-detection.

**`main()` entry point** — Every CLI script defines a `main()` function and guards invocation with `if __name__ == "__main__": main()`. This makes the operation logic importable without side effects. The MCP server imports the functions directly; `main()` is only for CLI use.

## Adding New Operations

Implement the logic as importable functions in a script, add a `main()` CLI entry point, then import into `brain_mcp/server.py`. Never put operation logic directly in the server.

This keeps the CLI and MCP paths identical: agents without MCP call the script directly and get the same result as agents using MCP.
