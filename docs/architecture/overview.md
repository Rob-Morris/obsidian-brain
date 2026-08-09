# Architecture Overview

## System Overview

brain-core is a self-extending system for organising Obsidian vaults, for agents and humans working together. It ships a versioned engine (`.brain-core/`) into each vault that provides MCP tools, CLI scripts, and a taxonomy-driven configuration layer. Together these give agents and human operators a shared, structured interface to vault content: creating and editing artefacts, searching by keyword, enforcing naming and status conventions, and bootstrapping agents with the minimum context needed to operate correctly.

---

## Component Map

### `.brain-core/` — the engine

Copied into the vault during setup and upgrade (not symlinked — vaults are self-contained and portable). `setup.py` and `configure.py` are the public workspace / client lifecycle surfaces; their shared launcher-safe ownership now lives under `_bootstrap/` (`vaults.py`, `workspace_scaffold.py`, `mcp_transport.py`, and `agent_skills.py`). `repair.py` is the explicit current-vault recovery entry point and bootstraps packageful repair back into the central managed runtime at `~/.brain/venvs/py<X.Y>-<sha16>/` (see [DD-048](decisions/dd-048-central-managed-runtime.md)) when needed. The no-MCP `brain session` bootstrap path uses a separate machine-owned resolution runtime at `~/.brain/resolution-runtime/` before dispatching to the resolved Brain's own `session.py` (see [DD-054](decisions/dd-054-machine-resolution-runtime.md)). Contains:

- `scripts/` — all vault operation logic as importable Python modules with CLI entry points
- `brain_mcp/server.py` + `brain_mcp/_server_*.py` — MCP composition root and sibling tool handlers; holds router and index in memory
- `skills/` — core skill documents (system-provided, tagged `"source": "core"`, overwritten on upgrade)
- `client-adapters/` — stable native-client discovery templates; executable workflows remain under `skills/`
- `index.md` — thin bootstrap entry point; routes agents to `brain_session`, `.brain/local/session.md`, or `md-bootstrap.md`
- `session-core.md` — checked-in authored source for the static core bootstrap content and core-doc references
- `md-bootstrap.md` — explicit degraded fallback for environments without MCP or a generated session mirror

### `.brain/` — vault-local runtime state

Generated, gitignored. The compiled outputs that tooling reads at runtime:

| Path | Contents |
|---|---|
| `.brain/local/compiled-router.json` | Compiled router — the interface contract between config and tooling |
| `.brain/local/session.md` | Generated markdown mirror of the canonical session model |
| `.brain/local/retrieval-index.json` | BM25 retrieval index for keyword search |
| `.brain/local/init-state.json` | Recorded MCP registrations owned by this vault for safe scoped removal |
| `.brain/config.yaml` | Vault-level configuration (layer 2 of 3) |
| `.brain/local/config.yaml` | Machine-local overrides (layer 3 of 3; gitignored) |
| `.brain/local/workspaces.json` | Workspace key-to-path registry |
| `.brain/local/mcp-server.log` | Rotating server log (2 MB max, 1 backup) with explicit startup phase markers |

### `_Config/` — user-customisable definitions

Instance configuration specific to this vault installation:

- `_Config/router.md` — lean bridge: capability detection, always-rules, conditional trigger gotos (~45 tokens)
- `_Config/Taxonomy/` — one file per artefact type with detailed instructions; loaded on demand
- `_Config/Skills/` — user-defined skill documents; discovered by the compiler alongside core skills
- `_Config/Memories/` — standing context injected by trigger matching
- `_Config/Styles/` — formatting style definitions
- `_Config/Templates/` — artefact creation templates
- `_Config/User/preferences-always.md` — vault owner's workflow preferences and quality standards
- `_Config/User/gotchas.md` — learned lessons from previous sessions

### `_Temporal/` — time-stamped artefacts

Working files in type subfolders under `_Temporal/`, each organised into `yyyy-mm/` month folders. Temporal types are discovered by scanning `_Temporal/` subfolders (distinct from the living-type scan).

### Living artefact folders — user content

Root-level folders without a `_` or `.` prefix are living artefact types (e.g. `Projects/`, `Research/`, `Ideas/`). Discovered by scanning the vault root — no registry required. Each type maps to a folder defined in `_Config/Taxonomy/`.

### `_Archive/` — archived artefacts

`_Archive/` is the deliberate-removal path for taking artefacts out of the active vault namespace. Routine terminal statuses usually move living artefacts into `+Status/` folders within their type namespace; archived artefacts are the separate subset intentionally moved to the top-level `_Archive/` tree by type and canonical child-folder structure. Archived files remain readable but are write-protected by the path security model and excluded from normal artefact operations.

---

## Data Flow

A typical MCP tool call follows this path:

```
MCP client request
  → brain_mcp/server.py — traces, gates, and delegates to the matching MCP handler
  → sibling MCP handler module — maps the tool to the relevant script call
  → script reads compiled router / retrieval index from in-memory state
  → operates on the vault filesystem (read, write, rename, etc.)
  → returns a structured response to the MCP client
```

The server now starts from a minimal runtime skeleton, answers MCP `initialize`, and then drives router/index/workspace/session maintenance as background warmup. Warmup emits stable begin/success/failure markers into `.brain/local/mcp-server.log`, which makes a stalled config load, router freshness pass, index rebuild, registry load, or session-mirror refresh diagnosable without extra instrumentation. Warmup-dependent MCP calls return structured progress/retry payloads while readiness is still `starting`, instead of blocking blindly on cold startup. Scripts called via MCP still pay no disk I/O for router or index reads once warmup has finished. Scripts called directly (without MCP) read the same JSON files from disk on each invocation — same logic, higher cold-start cost.

Mid-session, if `.brain-core/` is upgraded the server detects version drift on the next tool call and exits cleanly with code `10`. The MCP proxy interprets that as a planned restart and relaunches the server with the new code. The proxy uses one restart coordinator for every child-loss path (planned restart, crash, broken pipe, or startup failure), but the actual backoff/restart loop now lives on a dedicated recovery thread. The main stdin loop keeps reading while recovery runs, so requests that arrive during backoff or initial-start failure get the transient `server restarting, please retry` error immediately instead of queueing in the pipe. If backoff exhausts, the proxy switches to explicit restart-MCP guidance; if the recovery thread itself dies, the dead-child path surfaces a hard unrecoverable error instead of waiting forever.

---

## Key Architectural Properties

### Filesystem-first discovery

Artefact types are discovered by scanning vault folders, not by reading a registry. Root-level non-system folders become living types. `_Temporal/` subfolders become temporal types. The convention is: any top-level folder starting with `_` or `.` is infrastructure. `_Temporal/` follows this convention (excluded from the living-type scan) but receives its own dedicated scan for its children. This means adding a new artefact type requires only creating a folder and a taxonomy file — no registry update.

### Compiled router as contract

The compiled router (`.brain/local/compiled-router.json`) is the interface between human-readable config and all tooling. Source files — `session-core.md`, `router.md`, taxonomy files, skills, styles, memories, plugins, and `VERSION` — are the single source of truth. The compiler combines them into a hash-invalidated cache: SHA-256 of every source file is stored in `meta.sources`, and the cache is considered stale the moment any source changes. The router is environment-specific (includes platform, runtime availability, absolute vault root) and is never committed to version control. The MCP server auto-compiles it at startup and auto-recompiles mid-session when sources change or new resources appear; staleness is checked on a 5-second TTL via SHA-256 hashes for edits and a directory-mtime signature for additions/deletions, so the check itself stays cheap on stable vaults (DD-042).

### Scripts as single source of truth

The MCP server is a thin wrapper. All vault operation logic lives in `.brain-core/scripts/` as importable Python modules, each with a CLI entry point. The server imports functions from scripts and adds MCP transport, in-memory caching, process-local mutation serialization for mutating tool calls, and Obsidian CLI delegation. This means agents without MCP use the scripts directly and get identical results. New operations are always implemented as scripts first, then exposed via MCP — never the reverse.

The command-interface migration adds a transport-neutral application boundary
under `scripts/_application/` (DD-061). Typed request classes own command
identity/version/result type; adapters compose trusted `InvocationContext`
values and call `CommandApplication.invoke`. The boundary checks authority and
capability state before executor entry, validates structural
`brain.command-result/1` values, and records typed outcome receipts. It imports
no MCP SDK, parser, environment resolver or managed provider, and lower-level
packages never import back into it. The v0.54.1 foundation is internal only:
the existing MCP, CLI and direct-script flow above remains authoritative until
the coordinated breaking cutover. v0.54.2 extends that foundation with bounded
privacy-minimal outcome receipt retention/query semantics, strict dynamic
request resolution, independent compatibility-version rules and deduplicated
provider refresh. A separate stdlib-only `brain.launcher-catalogue/1` beside
the machine-global launcher owns its 23 pre-Brain/self-replacing operations;
it never imports or manufactures selected-Brain application executors.
v0.54.3 begins owner migration with catalogue-backed `command.list`,
`command.describe` and receipt-backed `invocation.read`; these are internal
application owners, not yet additional MCP or CLI surfaces.
v0.54.4 adds the first portable domain family: `artefact.read`,
`artefact.list` and `artefact.outline`. Adapter-free `_portable` modules own
their filesystem/filter/structural semantics; legacy scripts delegate down and
typed application executors normalise the shared result boundary.
v0.54.5 extends portable read ownership to `runtime.read-environment`,
`vault.read-router` and `links.check`. Environment/router results are exact
typed views, and link diagnosis remains independent of compiled-router health.
v0.54.6 adds separate `read` and `list` owners for skills, styles and plugins.
They share adapter-free named-document mechanics while retaining independent
request, result, executor and catalogue identities.
v0.54.7 adds exact memory and trigger collection owners. Memory reads use
canonical names; trigger reads use unique conditions; search remains a separate
semantic operation rather than an ambiguous read mode.
v0.54.8 separates exact active vault-file reads from archived-artefact reads
and archive listing. Exact vault-relative identity and archive membership are
validated before filesystem access, while legacy adapters delegate to the same
portable path and archive-discovery semantics.
v0.54.9 gives artefact types and templates separate exact-key read/list owners.
Type reads return the authored taxonomy definition through a bounded result,
and template results expose actual `.md` paths that compose with exact file
reads; legacy alias matching remains confined to the old adapters.
v0.54.10 separates workspace metadata reads, listing and path resolution.
Canonical workspace commands validate exact slugs, fail closed on corrupt
machine-local registry state and apply embedded-over-linked precedence once per
identity; legacy public adapters remain unchanged until cutover.
v0.54.11 completes the portable read-only catalogue group with bounded config,
vault compliance and artefact-library status owners. Config output excludes
operator/authentication and machine-path data; diagnostic recovery names
canonical commands rather than embedding shell commands.
v0.54.12 migrates `session.start` as a managed reader command with an honest
derived-cache-write effect. Its bootstrap result is fully typed, workspace
identity comes from trusted invocation context, and the canonical session model
and markdown mirror remain owned by `session.py`.
v0.54.13 completes the optional-semantic read group with granular search,
classification and duplicate-resolution owners. Portable lexical and taxonomy
paths remain complete; semantic enhancement requires trusted provider and
capability context before any selected-Brain semantic sidecar is loaded.
v0.54.14 begins typed mutation ownership with staging and attachment transfer.
Contributor authority, receipt-required retry, compact committed effects and
unknown-outcome handling are enforced at the application boundary while the
existing staging and attachment modules retain content/path semantics.
v0.54.15 adds separate memory, skill and style creation owners over immutable
inline/staged content and deterministic typed frontmatter fields. Existing
creation semantics remain canonical, including stale-router refusal and
post-commit staged-handle cleanup.
v0.54.16 adds create-only `template.create`: full-document content, type-linked
placement, no separate frontmatter and no overwrite. Legacy aggregate overwrite
behavior remains isolated until the coordinated breaking cutover.
v0.54.17 adds typed `artefact.create` with optional template-backed content,
explicit type/parent/key intent and bounded structural parent/link results. It
removes caller-file content from the new owner while retaining the existing
type, naming, placement and write semantics behind the application boundary.
v0.54.18 adds five distinct artefact document-mutation owners over typed
structural selectors and scopes. The shared application seam owns request,
staging, result and effect rules while `edit.py` continues to own body,
frontmatter, derived-path and wikilink behaviour.
v0.54.19 projects that seam into 20 concrete memory, skill, style and template
commands. Each domain/verb remains independently discoverable and typed; shared
inherited field contracts and bindings remove duplication without restoring a
cross-resource aggregate command.
v0.54.20 adds four explicit artefact lifecycle commands. Reparenting requires
the nullable parent field to be present, and all four commands retain the
existing lifecycle invariant engine as their single semantic owner.
v0.54.21 adds five operator artefact transitions. Requests are split by verb,
same-type rename policy belongs to the script semantic layer, and results
distinguish committed, known-partial and unknown effects structurally.

The lifecycle/bootstrap side of that script layer now has an explicit shared owner under `scripts/_bootstrap/`. `runtime.py` owns launcher discovery, managed-runtime handoff, executable path identity, and the shared `BRAIN_BOOTSTRAP_SUMMARY` contract; `diagnostics.py` owns the launcher-safe runtime/MCP/registry checks needed before managed semantic work is available; `mcp_state.py` owns shared MCP/config-layout and init-state helpers; `vaults.py` owns the env-aware vault-root discovery seam used by the public lifecycle wrappers; `workspace_scaffold.py` owns Brain-local ignore-rule convergence; `mcp_transport.py` owns the shared Claude/Codex transport/config write engine; and `agent_skills.py` owns version-neutral, ownership-safe client skill adapters. Entry points such as `setup.py`, `repair.py`, `configure.py`, `session.py`, and `check.py` now converge on that seam instead of carrying parallel launcher or env-var logic.

Managed operational wrappers now consume that same seam instead of assuming the caller already arranged the right interpreter. Retrieval wrappers (`build_index.py`, `search_index.py`, `construct_benchmark_fixture.py`, `evaluate_search.py`) and the remaining managed direct wrappers (`compile_router.py`, `compile_colours.py`, `sync_definitions.py`, `shape_printable.py`, `shape_presentation.py`, `migrate_naming.py`) all start in a compatible launcher Python only long enough to enter the canonical managed runtime, then continue substantive work there.

Within that script layer, retrieval ownership is now split honestly by
responsibility: lexical index and retrieval policy live under `scripts/_search/`,
semantic sidecar and local-model mechanics live under `scripts/_semantic/`,
and combined router + lexical + semantic refresh workflows plus the canonical
derived-cache and managed semantic inspect/repair/check owners live under
`scripts/_lifecycle/` (notably `derived_cache_state.py` and
`semantic_repairs.py`). The top-level `build_index.py`,
`search_index.py`, and `repair.py semantic` surfaces remain supported script
entrypoints, but they are thin wrappers over those canonical module owners
rather than the Python import surface. Internal production code and tests now
depend on `_search`, `_semantic`, and `_lifecycle` directly; the wrappers
remain as supported script surfaces only.

The optional [`brain` CLI](../functional/cli.md) (installed by `install.sh` to `~/.local/bin/brain` by default, `/usr/local/bin/brain` with `--system`, or skipped with `--skip-cli`) is a thin dispatch layer on top of these scripts — `brain repair runtime` reaches the same `repair.py` entry surface against the active vault's central managed runtime. The CLI versions independently from `brain-core`; its dispatch surface is the contract. See [DD-049](decisions/dd-049-brain-cli-thin-dispatch.md).

Repair ownership is split by altitude. `check.py` and `repair.py` diagnose or
repair vault-local state only: `.brain/local/workspaces.json`, the compiled
router, the lexical index, retrieval sidecars, and artefact frontmatter under
the selected vault root. Machine-wide state belongs to `machine.py`,
`doctor_machine.py`, and `vault_registry.py`: `$XDG_CONFIG_HOME/brain/vaults`
(default `~/.config/brain/vaults`), the `default` Brain pointer, and shared
managed runtimes under `~/.brain/venvs/`. Vault-scoped repair may read across
that line to report useful guidance, but it must not mutate machine-wide state.

### Three-layer config merge

Vault configuration is assembled from three layers at server startup:

1. **Template defaults** — built-in baseline values
2. **`.brain/config.yaml`** — vault-level configuration (committed with the vault)
3. **`.brain/local/config.yaml`** — machine-local overrides (gitignored)

Later layers override earlier ones. This lets vault owners set shared defaults while individual machines or operators override specific values without affecting others.

### Path security model

Two complementary guards protect the vault from unintended writes:

- **`resolve_and_check_bounds(path, bounds)`** — resolves symlinks and verifies the target is within the vault root. Raises `ValueError` if the resolved path escapes the boundary or is a symlink when symlink-following is disabled. Used on every read that accepts a caller-supplied path.
- **`check_write_allowed(rel_path)`** — enforces folder-level write restrictions. Dot-prefixed top-level folders (`.brain/`, `.obsidian/`, `.brain-core/`) are always blocked. Underscore-prefixed top-level folders are blocked unless in the explicit allowlist: only `_Temporal/` and `_Config/` are writable. `_Archive/`, `_Plugins/`, `_Workspaces/`, and `_Assets/` are protected from general writes. The separate `brain_upload_attachment` capability derives one validated `_Assets/Attachments/<scope>/<filename>` destination from a required artefact or folder key and does not broaden this allowlist ([DD-059](decisions/dd-059-attachment-upload-boundary.md)).
- **`safe_write(path, content, bounds=...)` / `safe_write_via(path, writer, bounds=...)`** — shared atomic write primitives (temp file + `os.replace`) that call `resolve_and_check_bounds` before writing. Text/JSON writes go through `safe_write(...)`; callback-driven serializers can use `safe_write_via(...)` for the same atomic replacement path.

---

## Agent Reading Flow

Agents bootstrap through one canonical session model with three operating modes:

1. **MCP bootstrap** — `brain_init` is the additive cheap orientation surface: it reports vault identity plus coarse readiness/warmup state, a static `bootstrap_hint`, and can optionally ensure warmup is underway. `brain_session` remains the canonical full session model as compact JSON: static core bootstrap content, structured core-doc references with explicit MCP load instructions, local workspace-configuration guidance, always-rules, user preferences, gotchas, triggers, condensed artefact types, environment, and config/profile metadata when known. The `workspace_configuration` record identifies the operation as local CLI work, gives the `brain configure workspace binding` command, and explicitly states that MCP cannot configure the connecting agent's filesystem. It never substitutes server filesystem paths into the client-local command. While warmup is still running, `brain_session` returns a structured progress/retry payload instead of blocking blindly. Coarse readiness policy for `router`, `index`, and semantic waits now lives behind one shared owner, so MCP handlers emit the same `needs` and `next_action` contract regardless of which warmup-dependent tool was called. Once ready, `brain_session` also refreshes `.brain/local/session.md` from the same model.
2. **CLI session fallback** — if MCP is unavailable from a bound external workspace, `brain session --json` runs the machine-level resolver, resolves the bound/default Brain, and dispatches only to that Brain's own `session.py`. This provides the JSON session model without a non-MCP `brain_init` twin.
3. **Generated markdown bootstrap** — if no JSON session path is available, agents read `.brain-core/index.md`, which routes them to `.brain/local/session.md`. That file is regenerated by normal runtime entry points (`brain_session`, `session.py`, router compile/startup paths), so it stays in parity with the JSON model for shared content.
4. **Degraded raw-file fallback** — if there is no MCP and no generated session mirror, `.brain-core/index.md` routes agents to `.brain-core/md-bootstrap.md`, which points them at `_Config/router.md`, user preferences, gotchas, and raw vault navigation.

All modes begin with the `AGENTS.md` bootstrap directive, which points agents to `brain_session` first and `.brain-core/index.md` as the stable no-MCP entry point.

---

## Cross-references

- `bounded-contexts.md` — bounded context map, responsibilities, and import policy
- `decisions/` — individual design decisions (rationale, trade-offs, status)
- `security.md` — detailed security model
- `../functional/` — tool and script reference documentation
