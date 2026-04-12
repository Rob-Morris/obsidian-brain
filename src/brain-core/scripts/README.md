# scripts/

Scripts are the **source of truth** for all vault operations. The MCP server (`brain_mcp/server.py`) is a thin wrapper that imports functions from these scripts and holds the compiled router and search index in memory. Scripts are the single implementation — the server adds only MCP transport, in-memory caching, and Obsidian CLI delegation. Agents without MCP use scripts directly and get identical results. New operations are implemented as scripts first, then exposed via MCP.

## Module Table

| Script | Purpose | CLI usage |
|---|---|---|
| `_common/` | Shared utilities package: vault discovery, frontmatter parsing, serialisation, BM25 tokenisation | (library only) |
| `build_index.py` | Build BM25 retrieval index | `python3 build_index.py [--json]` |
| `check.py` | Router-driven structural compliance checks | `python3 check.py [--json] [--severity S]` |
| `compile_colours.py` | Generate folder colour CSS | (called by compile_router) |
| `compile_router.py` | Compile router from source files and refresh session markdown | `python3 compile_router.py [--json]` |
| `config.py` | Vault configuration loader (three-layer merge) | `python3 config.py` |
| `create.py` | Create new artefact or `_Config/` resource | `python3 create.py --type T --title "Title" [--body B] [--body-file PATH] [--temp-path [SUFFIX]] [--json]` |
| `edit.py` | Edit/append/prepend to artefact or resource | `python3 edit.py edit\|append --path P --body B [--body-file PATH] [--temp-path [SUFFIX]] [--target H] [--json]` |
| `fix_links.py` | Auto-repair broken wikilinks | `python3 fix_links.py [--fix] [--json] [--vault V]` |
| `generate_key.py` | Generate operator key + hash for config.yaml | `python3 generate_key.py [--count N]` |
| `init.py` | MCP server registration | `python3 init.py [--user] [--local] [--project PATH]` |
| `list_artefacts.py` | Enumerate vault artefacts and resources (unranked, no cap) | (library module, used by MCP server) |
| `migrate_naming.py` | Migrate filenames to generous naming conventions | `python3 migrate_naming.py [--vault V] [--dry-run] [--json]` |
| `obsidian_cli.py` | IPC client for native Obsidian CLI | (library module, used by MCP server) |
| `process.py` | Content classification, duplicate resolution, ingestion | (library module, used by MCP server) |
| `read.py` | Query compiled router resources | `python3 read.py RESOURCE [--name N]` |
| `rename.py` | Rename/delete file + update wikilinks | `python3 rename.py "source" "dest" [--json]` |
| `search_index.py` | BM25 keyword search | `python3 search_index.py "query" [--type T] [--json]` |
| `session.py` | Build the canonical session model and refresh `.brain/local/session.md` | `python3 session.py [--json]` |
| `shape_presentation.py` | Create presentation + launch Marp preview | (via MCP: `brain_action("shape-presentation", ...)`) |
| `start_shaping.py` | Bootstrap a shaping session for an existing artefact | (via MCP: `brain_action("start-shaping", ...)`) |
| `sync_definitions.py` | Sync artefact library definitions to vault `_Config/` | `python3 sync_definitions.py [--vault V] [--dry-run] [--types t1,t2] [--json]` |
| `upgrade.py` | In-place brain-core upgrade with local migration ledger | `python3 upgrade.py --source P [--vault V] [--dry-run] [--force] [--json]` |
| `workspace_registry.py` | Workspace slug→path resolution | `python3 workspace_registry.py [--register SLUG PATH] [--unregister SLUG] [--resolve SLUG] [--json]` |

## Bounded Context Map

The script layer is organised into 8 bounded contexts. This is an architectural ownership map, not a packaging requirement.

| Context | Scripts |
|---|---|
| Compilation | `compile_router.py`, `compile_colours.py`, `build_index.py`, `sync_definitions.py` |
| Artefact Operations | `create.py`, `edit.py`, `read.py`, `rename.py`, `fix_links.py`, `start_shaping.py`, `shape_presentation.py` |
| Compliance | `check.py` |
| Content Intelligence | `search_index.py`, `list_artefacts.py`, `process.py` |
| Session & Configuration | `session.py`, `config.py`, `workspace_registry.py`, `generate_key.py` |
| Lifecycle Management | `init.py`, `upgrade.py`, `migrate_naming.py`, `migrations/` |
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
- `check.py`
- `compile_colours.py`
- `compile_router.py`
- `config.py`
- `create.py`
- `edit.py`
- `fix_links.py`
- `init.py`
- `list_artefacts.py`
- `migrate_naming.py`
- `process.py`
- `read.py`
- `rename.py`
- `search_index.py`
- `session.py`
- `shape_presentation.py`
- `start_shaping.py`
- `sync_definitions.py`
- `workspace_registry.py`

### Standalone (no `_common` dependency)

- `generate_key.py` — stdlib only
- `obsidian_cli.py` — stdlib only; IPC socket client
- `upgrade.py` — deliberately self-contained (it may replace `_common` during execution); duplicates only `find_vault_root()`, and records per-migration history in `.brain/local/` so reinstalls do not replay migrations unless forced

## `_common/` Package Structure

The `_common/` package decomposes shared utilities into focused modules. `__init__.py` re-exports all public names, so consumers continue to `from _common import …` unchanged.

Boundary rule:
- `__init__.py` exports only supported public API.
- Underscore-prefixed helpers stay module-internal by default.
- If another script genuinely needs a helper across module boundaries, promote it to a public name rather than importing a private helper through the facade.

| Module | Purpose | Functions |
|--------|---------|-----------|
| `_vault.py` | Vault root discovery, version, scanning, artefact matching | 8 |
| `_artefacts.py` | Shared artefact naming, folder resolution, config-resource paths, file reads | 5 |
| `_router.py` | Compiled router loading, naming-pattern matching, artefact path validation | 6 |
| `_filesystem.py` | Safe writes, bounds checking, body file resolution | 7 |
| `_frontmatter.py` | Frontmatter parsing and serialisation | 2 |
| `_wikilinks.py` | Wikilink extraction, file index, broken link resolution | 17 |
| `_markdown.py` | Heading collection, fenced ranges, section finding | 4 |
| `_slugs.py` | Slug generation, title-to-filename conversion | 3 |
| `_search.py` | BM25 tokenisation | 1 |
| `_templates.py` | Timestamp utilities, template variable substitution | 3 |

Internal dependencies flow from leaves to integrators:

```
_slugs, _search, _markdown, _frontmatter, _templates, _vault  (standalone)
_artefacts   → _slugs, _vault
_router      → _wikilinks
_filesystem  → _vault
_wikilinks   → _vault, _filesystem, _slugs
```

Tests may import owning submodules directly when validating internal helpers.

## Shared Patterns

**`--json` flag** — All CLI-facing scripts that produce output accept `--json` to emit a machine-readable JSON result instead of human-readable text. Library-only modules do not expose this flag.

**`--vault` and `find_vault_root()`** — Vault location is auto-detected via `_common.find_vault_root()`, which walks up from the current directory looking for `.brain/`. Scripts accept `--vault` to override this. The MCP server passes the vault path explicitly; standalone CLI invocations rely on auto-detection.

**`main()` entry point** — Every CLI script defines a `main()` function and guards invocation with `if __name__ == "__main__": main()`. This makes the operation logic importable without side effects. The MCP server imports the functions directly; `main()` is only for CLI use.

## Adding New Operations

Implement the logic as importable functions in a script, add a `main()` CLI entry point, then import into `brain_mcp/server.py`. Never put operation logic directly in the server.

This keeps the CLI and MCP paths identical: agents without MCP call the script directly and get the same result as agents using MCP.
