# scripts/

Scripts are the **source of truth** for all vault operations. The MCP server (`brain_mcp/server.py`) is a thin wrapper that imports functions from these scripts and holds the compiled router and search index in memory. Scripts are the single implementation — the server adds MCP transport, in-memory caching, shared vault-scoped mutation serialization, and Obsidian CLI delegation. Agents without MCP use scripts directly and get identical results. New operations are implemented as scripts first, then exposed via MCP.

Direct script invocation remains the baseline command-line contract:

```bash
python3.12 .brain-core/scripts/<script>.py ...
```

The new command architecture is being built behind the existing public grammar
under `_application/`. That stdlib-only package owns typed request identity,
trusted invocation context, structural results, receipts and static
selected-Brain catalogue contracts. Its shared `CommandApplication.invoke`
boundary performs authority/capability checks and result normalisation without
importing MCP, CLI parsing, environment resolution or managed providers.
The v0.54.2 extension adds strict transport resolution, independently owned
compatibility versions, bounded provider refresh and privacy-minimal receipt
retention/query semantics. The separate machine-global launcher catalogue
lives with the launcher source under `cli/`; it is not imported into this
selected-Brain package.
The first v0.54.3 owner batch makes `command.list`, `command.describe` and
`invocation.read` real catalogue-backed application commands. They remain
internal until public projection cutover, but subsequent Phase 3 owners now
join the same authoritative catalogue and discovery path.
v0.54.4 migrates `artefact.read`, `artefact.list` and `artefact.outline` onto
that path. Their legacy top-level scripts delegate semantic work to
adapter-free `_portable` modules, while typed `_application/artefact/` owners
normalise results and effects without importing `argparse` or MCP.
v0.54.5 adds `runtime.read-environment`, `vault.read-router` and `links.check`.
Runtime/router views now have an adapter-free portable owner and link checks
reuse the existing portable scanner without requiring router availability.
v0.54.6 adds separate read/list owners for skills, styles and plugins. Their
exact-name reads and filtered lists share `_portable/named_documents.py`, and
the legacy reader/lister delegate to that seam without changing public output.
v0.54.7 adds exact memory/trigger read/list owners over portable router
collection views. Trigger reads now select by unique condition and trigger list
queries search real category/condition/detail/target fields.
v0.54.8 adds exact vault-file and archived-artefact read/list owners. Active
file reads and archive reads enforce distinct path domains, and legacy readers
and listers delegate to one portable containment and archive-discovery seam.
v0.54.9 adds separate exact-key read/list owners for artefact types and
templates. Type reads include the authored taxonomy definition, template
results expose composable `.md` paths, and legacy aliases remain adapter-local.
v0.54.10 adds distinct workspace read/list/resolve owners. Canonical workspace
commands validate exact slugs, fail closed on corrupt local registry state and
apply embedded-over-linked precedence consistently.
v0.54.11 completes portable read ownership with privacy-bounded config,
typed compliance findings and exact artefact-library status. Repair guidance
uses canonical command identifiers instead of executable shell strings.
v0.54.12 adds a typed managed `session.start` owner with trusted workspace
context and an explicit derived-cache-write effect while retaining
`session.py` as the bootstrap model and markdown-rendering authority.
v0.54.13 adds granular typed search owners plus structural content
classification and duplicate resolution. Portable lower paths remain complete,
and semantic enhancement is gated by trusted provider/capability context.
v0.54.14 adds typed contributor mutation owners for staged bodies and
attachments, with explicit effects, dry-run behavior and unknown-outcome
handling over the existing staging and attachment semantic modules.
v0.54.15 adds granular memory, skill and style creation owners with immutable
inline/staged content, bounded typed frontmatter and commit-safe staged-handle
consumption over the existing `create.py` semantic owner.
v0.54.16 adds create-only typed template ownership over the same content seam,
while preserving legacy aggregate overwrite behavior solely until cutover.
v0.54.17 adds granular artefact creation with optional template-backed content,
typed parent/link results and commit-safe inline/staged mutation handling.
v0.54.18 adds granular artefact edit, append, prepend, delete-section and
replace-text owners over shared typed structural mutation mechanics.
v0.54.19 adds the corresponding 20 memory, skill, style and template owners,
completing granular internal replacement of the legacy edit aggregate.
v0.54.20 adds explicit artefact reparent, status, key and naming-field owners.
Required-but-nullable parent intent distinguishes omission from deliberate
ownership clearing, while `edit.py` retains lifecycle and derived-move rules.
v0.54.21 adds operator-owned rename, convert, archive, unarchive and delete
commands with structural path sets and explicit partial/unknown outcomes.
v0.54.22 completes operator artefact maintenance ownership with explicit
child-reparent modes, bounded repair/naming results and typed link preview/fix.
v0.54.23 splits plugin and trigger definition mutation into five operator-owned
commands with staged-content safety, optimistic hashes and exact trigger intent.
v0.54.24 adds atomic `type.create` and `type.replace` owners for paired taxonomy
and template documents with independent hashes and staged-handle finalisation.
v0.54.25 separates explicit artefact-library install from singular type sync,
including force-correct local customisation handling and structural outcomes.
v0.54.26 separates router repair from unconditional rebuild over one portable
compile, invalidation and session-refresh semantic owner.
v0.54.27 separates lexical-index repair from unconditional rebuild over one
portable construction, persistence and semantic-invalidation owner.
v0.54.28 adds rollback-safe `workspace.repair-registry` ownership, distinct
from caller-local workspace configuration and setup operations.
v0.54.29 adds typed `shaping.start` ownership over the existing portable
lifecycle, same-day transcript and backlink mechanics.
v0.54.30 adds managed `content.ingest` ownership with staged-content safety and
corrects lexical duplicate evidence so BM25 scores cannot authorise updates.
v0.54.31 adds distinct managed semantic enable, repair and rebuild owners,
extracts semantic opt-in lifecycle semantics from the configure adapter and
preserves honest partial effects when provisioning fails after configuration.
v0.54.32 adds distinct managed printable and presentation rendering owners,
requires the document-renderer provider and preserves family-specific controls
plus granular markdown, PDF and preview-process effects.
v0.54.33 adds managed retrieval benchmark construction and evaluation owners,
with explicit non-MCP projection metadata, selected-Brain path bounds and
separate mutation versus effect-free result contracts.
v0.54.34 adds the six caller-local workspace mutation owners. Their target
directory comes only from trusted invocation context backed by the explicit
`caller_filesystem` provider; request payloads cannot inject host paths. They
remain bootstrap-capable CLI/script/Python commands and explicitly exclude MCP.
v0.54.35 establishes the separate stdlib-only machine-global launcher
invocation boundary and its first six read owners under `cli/_launcher/`.
Selected-Brain `_application` does not import or duplicate that authority.
v0.54.36 adds the six machine Brain-registry mutation owners, with exact
change/no-op state, caller-filesystem capability preflight and receipt-safe
partial/unknown outcome reporting. The existing `vault_registry.py` scalar
functions and direct-script behaviour remain available over the new structured
action seams.
v0.54.37 adds the remaining effect-free launcher owners. Canonical
`brain.doctor` uses `inspect_machine_registry` through a non-synchronising
machine summary, so diagnosis cannot repair `brains.json`; typed findings omit
shell commands in favour of canonical repair command IDs. The reusable
`generate_key_material` seam supplies bounded `operator.generate-key` results
while preserving the existing direct script output.
v0.54.38 adds typed `agent-skill.configure` launcher ownership over the shared
`_bootstrap/agent_skills.py` seam. That seam now exposes genuine no-write
planning, while trusted home context, all-client preflight and receipt-safe
adapter/backup effects remain in the launcher owner.
v0.54.39 adds typed `machine.prune-runtimes` ownership over the existing
read-only topology and pruning seams. Trusted current-Brain context prevents an
unregistered active Brain from appearing orphaned; live-scan failure blocks
deletion and recursive-removal failure remains outcome-unknown.
v0.54.40 adds typed `machine.migrate-legacy` ownership over the same read-only
discovery seam and the existing target-Brain repair composition. Spawn failure
proves no effect, known child partials retain coarse repair-scope effects, and
timeouts, invalid child output or recursive-removal failure remain
outcome-unknown.
v0.54.41 adds typed `runtime.repair` launcher ownership over the bootstrap
runtime orchestrator. The selected Brain remains trusted context, no managed
process hand-off is hidden inside the owner, and the lower seam now classifies
no-effect, committed, known-partial and unknown mutation outcomes. An unusable
existing runtime fails closed instead of being replaced without live-use proof.
Existing adapters are not cut over in the v0.54.x foundation releases; they
continue to behave as documented until the coordinated breaking release
replaces the old grammar.
v0.54.42 adds typed `mcp.configure` and `mcp.repair` launcher ownership over a
shared bootstrap file transaction. It preflights every JSON, TOML, Markdown and
init-state input, performs deterministic direct edits, rolls all written files
and created directories back on failure, and reports exact surviving paths if
rollback fails. These owners require an already healthy managed runtime and do
not silently provision it, bind workspaces, edit ignore rules or invoke client
CLIs. Recorded removal remains available without a healthy runtime or binding.
v0.54.43 completes launcher lifecycle ownership with distinct `brain.install`,
`brain.uninstall` and `brain.upgrade` commands. Install uses a trusted complete
distribution and an unlocked no-write registry preview; uninstall preflights
and removes only selected-Brain system roots after exact MCP/registry cleanup;
upgrade runs the trusted source upgrader and transactionally refreshes the
known CLI path while retaining executable mode. Existing public adapters remain
unchanged until coordinated cutover.
v0.54.44 adds `_application/projection.py` as the shared internal projection
boundary. It maps canonical command identity mechanically, derives strict
request schemas from sealed request dataclasses and serialises every application
result through one deterministic structural envelope. Existing public adapters
remain unchanged until coordinated cutover.
v0.54.45 expands the internal `command.list` and `command.describe` owners into
authoritative typed discovery. Lists use one retained capability snapshot and
descriptions derive schemas and minimal resolver-checked examples from the
owning catalogue/request types. Public adapters remain unchanged until cutover.
v0.54.46 adds `_application/adapter.py` as the only dynamic request/result seam
beneath future MCP, CLI and direct-script projections. It preserves sealed
request invocation, canonical envelopes, concise rendering, MCP error state and
the stable CLI exit categories without owning trusted context composition.
v0.54.47 adds `_command_interface/` as the concrete local adapter package. It
composes only already-resolved selected-Brain, profile, tier, provider and
capability state into `_application`, and persists effect-bearing outcome
receipts in a bounded privacy-minimal store under `.brain/local/`. The
application package does not import this outward-facing composition layer.
v0.54.48 adds the staged `command.py <noun> <verb>` direct projection. It uses
one catalogue/resolver instance, accepts one strict JSON request object, emits
canonical JSON or concise human output with stable exit categories, and never
silently provisions or hands off runtimes. Default discovery does not probe;
explicit refresh deduplicates provider checks. The global CLI and current MCP
remain on their existing public surfaces until the coordinated cutover.
v0.54.49 adds staged catalogue-derived granular FastMCP registration. Raw MCP
arguments resolve through the canonical request seam rather than a second
Pydantic request contract; public aggregate registration remains unchanged.
v0.54.50 ships a checked static `command-catalogue.json` route for typed
`session.start` v2. It contains only bounded discovery facts and list/describe
directions, imports no command owners at bootstrap, and remains hidden from the
legacy session adapter until coordinated cutover.
v0.54.52 adds launcher-owned list/describe projection and an outer
`cli/_local_cli/` composition boundary. Launcher discovery derives schemas and
examples from its own sealed request/result types; the outer view retains both
catalogue provenances and never imports selected-Brain application semantics.
v0.54.53 stages catalogue-derived built-in granular profile sets and moves
known-command authority denial ahead of dynamic request resolution. The
current aggregate defaults remain unchanged until coordinated cutover.
v0.54.54 stages owner-preserving local CLI execution. Launcher commands use a
launcher-owned dynamic adapter; application commands cross a process boundary
to the selected Brain's own `command.py`. The outer CLI validates provenance,
result identity and exit categories without importing `_application`.
v0.54.55 makes known semantic request failures structural across typed Python,
dynamic, direct-script, FastMCP and composed local CLI paths. The outer CLI is
the sole JSON/human stream owner and rejects child stderr or result/exit drift.
v0.54.56 stages a pure one-time granular profile migration. Exact historical
built-ins become catalogue-derived sets; custom profiles expand only explicit
legacy capabilities plus required outcome-query closure. Runtime fallback and
pre-cutover config mutation remain prohibited.
v0.54.57 stages `brain command list/describe` parsing with explicit composed
owner selection. The outer CLI projects filters separately to each catalogue,
keeps refresh application-owned and raises non-exiting category-2 usage errors.
v0.54.59 activates `brain.command-interface-header/1` as the strict
proxy/server contract derived from the selected-Brain catalogue. The
replacement server emits the header and blocks incompatible live proxies before
tool lookup; proxy 0.6.0 marks its running protocol, validates accepted command
identity and permits only compatible planned-drift replay. Unexpected read loss
has one bounded retry, while mutation loss queries durable receipts and is never
blindly replayed. The public command grammar remains staged for cutover.

That launcher process is not automatically the managed runtime. The shared launcher-safe bootstrap ownership now lives under `_bootstrap/`: bootstrap entrypoints do meaningful launcher-safe work there, and runtime-owning lifecycle entrypoints such as `repair.py`, `setup.py`, `configure.py`, `session.py`, and `check.py` hand substantive managed work off into the canonical managed runtime before continuing.

Managed operational wrappers now follow that same contract too: `build_index.py`, `search_index.py`, `construct_benchmark_fixture.py`, `evaluate_search.py`, `compile_router.py`, `compile_colours.py`, `sync_definitions.py`, `shape_printable.py`, `shape_presentation.py`, and `migrate_naming.py` start in the launcher only long enough to enter the managed runtime. Manually activating the vault venv still works for debugging, but it is no longer the normal direct-script contract these wrappers document or rely on.

Semantic and hybrid retrieval remain optional. Enable them from an installed
vault with `python3 .brain-core/scripts/configure.py semantic --enable`
before using embedding-backed search or evaluation flows. That command writes
the local semantic-retrieval flag, installs the pinned semantic Python stack,
snapshots the pinned model under `.brain/local/semantic-models/`, records
`.brain/local/semantic-model-manifest.json`, and refreshes embeddings sidecars.
After provisioning, ordinary search/process/index paths load the local snapshot
with no surprise Hugging Face fetches. Missing sidecars still degrade cleanly;
present-but-corrupt sidecars now fail explicitly at search/evaluation entry
points so the owning boundary can rebuild or repair them deliberately. Query
snippets remain a presentation-only soft-degrade path: unreadable snippet
reads omit `snippet` instead of fabricating text, while persisted retrieval
state builds now fail visibly instead of silently skipping files or serving
stale state. The
pinned stack targets current upstream wheel-supported platforms; Intel macOS
remains lexical-only.

## Module Table

| Script | Purpose | CLI usage |
|---|---|---|
| `_application/` | Transport-neutral selected-Brain application contracts: typed request identity, explicit trusted context/provider ports, structural `brain.command-result/1` variants, bounded outcome receipts, capability refresh, independent version rules, strict dynamic resolution, static catalogue values, and the shared invocation boundary. Imports remain stdlib-only and package initialisation is intentionally lazy. | (library only; public adapter cutover pending) |
| `_bootstrap/` | Shared launcher-safe bootstrap package: env-aware vault discovery, workspace-local scaffold/ignore rules, managed-runtime handoff, bootstrap diagnostics, shared MCP/config-layout state, the Claude/Codex transport engine, and ownership-safe native-skill discovery adapters | (library only) |
| `_common/` | Shared utilities package: vault discovery, frontmatter parsing, serialisation, CLI parser helpers, and general script support | (library only) |
| `_lifecycle_common.py` | Shared lifecycle result-envelope rendering and CLI emission helpers | (library only) |
| `_repair_common.py` | Launcher-safe repair metadata, scope definitions, and exact command builders | (library only) |
| `_repair_runtime.py` | Managed-runtime repair scope implementations for the remaining non-semantic scopes; router, lexical and registry scopes delegate to their portable command-owner seams | (library only) |
| `_search/` | Internal retrieval package: lexical index ownership plus retrieval query-mode policy and lexical/semantic/hybrid execution | (library only) |
| `_semantic/` | Internal semantic package: config flags, model/runtime provisioning, semantic sidecar mechanics, and vector-ranking/runtime utilities shared by build/search/configure/repair flows | (library only) |
| `_lifecycle/` | Internal lifecycle/orchestration package: derived-cache state inspection, retrieval document-part types, duplicate-frontmatter repair helpers, shared retrieval-state errors, combined lexical+semantic refresh workflows, and the canonical managed semantic inspect/repair/check owner | (library only) |
| `_machine/` | Launcher-safe machine-management package: multi-Brain discovery, shared-runtime topology classification, and machine-level maintenance analysis/mutation orchestration beneath the CLI family. Maintains `$XDG_CONFIG_HOME/brain/brains.json` as the derived machine registry once Python handoff succeeds; the shell still bootstraps from the user-home `vault_registry.py` signal first, then falls back to `brains.json` only when no curated source Brain remains. Reuses launcher-safe per-vault diagnostics only for brain-level MCP/workspace drift so `brain doctor` can point each Brain back to its own repair path, and delegates Brain-owned MCP/runtime/registry repair back to each target Brain during `brain machine` mutations. | (library only) |
| `_portable/` | Portable operational package: launcher-safe seams shared by portable script surfaces, including canonical router, lexical-index and workspace-registry maintenance | (library only) |
| `build_lexical_index.py` | Thin portable lexical-only wrapper over `_search.index`: build the shared lexical retrieval index without any semantic or managed-runtime assumptions. | `python3 build_lexical_index.py [--json]` |
| `build_index.py` | Thin CLI/script wrapper over the retrieval lifecycle seam: build the lexical retrieval index and refresh embeddings sidecars from the provisioned local semantic model when `semantic_processing` or `semantic_retrieval` is enabled and router data is available. Unreadable source files, compiled-router embedding drift, and retrieval-index persistence failures now fail explicitly at this boundary. Use `_search.index` / `_lifecycle.retrieval_assets` directly from Python; the wrapper remains only as a supported script entry surface. | `python3 build_index.py [--json]` |
| `construct_benchmark_fixture.py` | Derive a vault-native retrieval benchmark fixture plus audit JSON from an existing vault, including semantic-variant audit diagnostics and optional externally seeded semantic or hybrid candidates. Unreadable source files now fail explicitly instead of being skipped silently. | `python3 construct_benchmark_fixture.py --fixture-out PATH [--audit-out PATH] [--semantic-strategy S] [--semantic-seed-file PATH] [--hybrid-seed-file PATH] [--json]` |
| `evaluate_search.py` | Benchmark lexical, semantic, and hybrid retrieval against a JSON query set | `python3 evaluate_search.py --benchmark PATH [--mode M]... [--json]` |
| `check.py` | Router-driven structural compliance checks; launcher-safe bootstrap diagnostics run first, derived router/lexical cache drift points at exact repair scopes, then managed semantic findings from the canonical semantic owner are added after managed-runtime handoff, and human output still prints exact `repair.py` commands for repairable drift | `python3 check.py [--json] [--actionable] [--severity S] [--vault V]` |
| `configure.py` | Explicit installed-vault lifecycle entry point: targeted `workspace binding`, `workspace metadata`, `workspace bootstrap`, `mcp`, `agent-skills`, and `semantic` surfaces without going through the setup wrapper | `python3 configure.py {workspace,mcp,agent-skills,semantic} ...` |
| `compile_colours.py` | Generate folder colour CSS | (called by compile_router) |
| `compile_router.py` | Compile router from source files and refresh session markdown | `python3 compile_router.py [--json]` |
| `config.py` | Vault configuration loader (three-layer merge) using the shared Brain-owned YAML seam for standalone config files | `python3 config.py` |
| `doctor.py` | Launcher-safe composed Doctor owner used by `brain doctor` when a source Brain is available; renders CLI/PATH/Python basics, machine-level shared-runtime diagnosis from `doctor_machine.py`, and the current vault's own `check.py` as a separate vault-local section | (internal helper, called by `brain doctor`) |
| `doctor_machine.py` | Launcher-safe machine-level runtime diagnosis adapter used by `doctor.py` / `brain doctor`; reports shared-runtime topology from the `_machine/` substrate, surfaces per-Brain drift with exact `repair.py` guidance, and leaves the CLI shell/bootstrap fallback intact | (internal helper, called by `brain doctor`) |
| `machine.py` | Launcher-safe machine-level maintenance entrypoint used by `brain machine`; currently exposes `migrate-legacy` (delegate per-Brain repair, remove legacy `.venv`, verify central-runtime convergence) and `prune-runtimes` (remove shared runtimes already proven orphaned) | `python3 machine.py --vault SOURCE [--current-vault V] [--launcher PY] {migrate-legacy,prune-runtimes} [...]` |
| `create.py` | Create new artefact or `_Config/` resource; living artefact keys use the clearest free title-derived words before random suffix fallback; parented temporal artefacts file under their owner chain before the month folder; mutation mode refuses stale compiled router state | `python3 create.py --type T --title "Title" [--body B] [--body-file PATH] [--parent NAME] [--vault PATH] [--temp-path [SUFFIX]] [--json]` |
| `define.py` | Guarded type-bundle, trigger, and plugin definition authoring with fixed destinations and optimistic replacement preconditions | `python3 define.py {type,trigger,plugin} ... [--vault PATH] [--json]` |
| `edit.py` | Strict structural and exact-text edits; handler-owned lifecycle fields are rejected | `python3 edit.py edit\|append\|prepend\|replace_text\|delete_section [...]` |
| `outline.py` | List exact structural selectors accepted by edit | `python3 outline.py PATH [--json]` |
| `stage.py` | Create a retry-safe opaque body handle | `python3 stage.py (--body B\|--body-file P)` |
| `discard_stage.py` | Release an unused staged body before expiry | `python3 discard_stage.py HANDLE` |
| `upload_attachment.py` | Add a caller-owned non-markdown file beneath a required artefact or standalone attachment scope | `python3 upload_attachment.py --destination-key K (--file P\|--content-base64 B) [--name N] [--vault V] [--json]` |
| `lifecycle.py` | Explicit parent/status/key/naming-field mutations | `python3 lifecycle.py {reparent,set-status,set-key,set-naming-field} ...` |
| `fix_links.py` | Auto-repair broken wikilinks; refuses stale compiled router state before scanning or applying vault-wide fixes | `python3 fix_links.py [--fix] [--json] [--vault V]` |
| `generate_key.py` | Generate operator key + hash for config.yaml via the dependency-free shared auth helper | `python3 generate_key.py [--count N]` |
| `install.py` | Shared Python installer core used by `install.sh` and `install.ps1`; normal users invoke a platform launcher, while the core owns scaffold/runtime/MCP policy and lifecycle output | `python3 install.py VAULT [--source-root REPO] [--launcher PY] [--mcp-scope {project,user,skip}] [--client {claude,codex,all}] [--id ID] [--json]` |
| `list_artefacts.py` | Enumerate vault artefacts and resources (unranked, no cap) via the same resource/filter contract as `brain_list` | `python3 list_artefacts.py [RESOURCE] [--query Q] [--type T] [--parent P] [--since D] [--until D] [--tag TAG] [--top-k N] [--sort S] [--vault V] [--json]` |
| `search_lexical.py` | Thin portable lexical-only wrapper over `_search.lexical_query`: query the shared lexical retrieval index with lexical filters only. | `python3 search_lexical.py "query" [--type T] [--tag TAG] [--status S] [--top-k N] [--json]` |
| `migrate_naming.py` | Migrate filenames to generous naming conventions | `python3 migrate_naming.py [--vault V] [--dry-run] [--json]` |
| `migrations/migrate_to_0_40_8.py` | v0.40.8 migration: merges duplicate nested artefact frontmatter blocks into canonical document frontmatter and strips the accidental body-level copy | `python3 migrations/migrate_to_0_40_8.py [--vault V] [--dry-run]` |
| `migrations/migrate_to_0_50_0.py` | v0.50.0 recursive owner-folder migration: backfills safe missing living `parent:` fields, validates parent chains and move sets, relocates living descendants into recursive owner paths, and prunes empty vacated owner folders | `python3 migrations/migrate_to_0_50_0.py [--vault V] [--dry-run] [--json]` |
| `obsidian_cli.py` | IPC client for native Obsidian CLI | (library module, used by MCP server) |
| `process.py` | Experimental content classification, duplicate resolution, ingestion | (library module, used by MCP server) |
| `repair.py` | Explicit named repairs, including preview/apply metadata-authoritative ownership projection | `python3 repair.py {runtime,mcp,router,lexical,registry,frontmatter,semantic,ownership} [...]` |
| `read.py` | Query compiled router resources; trigger reads use the exact condition as `--name`; read failures use stderr and a non-zero exit | `python3 read.py RESOURCE [--name N] [--vault V]` |
| `rename.py` | Rename/delete file + update wikilinks, refusing stale compiled router state and unsafe move sets before rewrites: collisions, duplicate/cyclic moves, symlink endpoints, and non-directory destination parents. Wikilink aliases are dropped when a rewrite would otherwise insert `|` into a markdown table cell. | `python3 rename.py "source" "dest" [--json]` |
| `search_index.py` | Thin CLI/script wrapper over `_search`: lexical, semantic, or hybrid local search with exact-anchor lexical wins, strong semantic champions, Brain-only title champions, and semantic-rescue fusion for disjoint leaders. Use `_search.lexical_query`, `_search.semantic_query`, `_search.hybrid_query`, `_search.mode`, and related helpers directly from Python; the wrapper remains only as a supported script entry surface. | `python3 search_index.py "query" [--type T] [--tag TAG] [--status S] [--mode M] [--top-k N] [--json]` |
| `session.py` | Build the canonical session model and refresh `.brain/local/session.md`; keeps a launcher-safe SessionStart shim but hands substantive work off into the managed runtime. Cross-Brain workspace resolution is owned by `brain session` before it dispatches with `--vault`. | `python3 session.py --vault V [--json] [--workspace-dir PATH]` |
| `shape_printable.py` | Create printable + render PDF | `python3 shape_printable.py --source P --slug S [--no-render] [--pdf-engine E] [--keep-heading-with-next]` |
| `shape_presentation.py` | Create presentation + render PDF + launch Marp preview | `python3 shape_presentation.py --source P --slug S [--no-render] [--no-preview]` |
| `start_shaping_session.py` | Open or continue a lifecycle-safe shaping session for an existing, shapeable artefact | `python3 start_shaping_session.py --target P --mode brainstorm\|refine\|discover [--vault V]` |
| `start_shaping.py` | Compatibility launcher for `start_shaping_session.py` | `python3 start_shaping.py --target P [--mode brainstorm\|refine\|discover] [--vault V]` |
| `sync_definitions.py` | Sync artefact library definitions to vault `_Config/`, using raw tracked hashes plus markdown-aware comparison for `.md` files so harmless table-padding rewrites do not surface as drift | `python3 sync_definitions.py [--vault V] [--dry-run] [--force] [--types t1,t2] [--status] [--json]` |
| `upgrade.py` | Canonical brain-core upgrade entry point with migrations, binary-safe rollback snapshots, runtime/retrieval reconciliation, and structured recommended follow-ups when a checked-in client adapter is introduced or changed | `python3 upgrade.py --source P [--vault V] [--dry-run] [--force] [--sync\|--no-sync] [--sync-deps\|--no-sync-deps] [--json]` |
| `vault_registry.py` | User-home authoritative Brain registry (`$XDG_CONFIG_HOME/brain/vaults`, default `~/.config/brain/vaults`). Current shipped writer stores typed `local` entries as `<brain-id>\tlocal\t<absolute-vault-path>`; legacy two-column local entries are still read for compatibility. | `python3 vault_registry.py [--register PATH\|--backfill PATH\|--unregister PATH\|--list [--json]\|--prune\|--resolve BRAIN_ID]` |
| `workspace_registry.py` | Workspace slug→path resolution | `python3 workspace_registry.py [--register SLUG PATH] [--unregister SLUG] [--resolve SLUG] [--json]` |

## Bounded Context Map

The script layer is organised into 8 bounded contexts. This is an architectural ownership map, not a packaging requirement.

| Context | Scripts |
|---|---|
| Compilation | `compile_router.py`, `compile_colours.py`, `build_index.py`, `sync_definitions.py` |
| Artefact Operations | `create.py`, `edit.py`, `read.py`, `rename.py`, `fix_links.py`, `upload_attachment.py`, `start_shaping_session.py` (`start_shaping.py` compatibility launcher), `shape_printable.py`, `shape_presentation.py` |
| Compliance | `check.py` |
| Content Intelligence | `_search/`, `search_lexical.py`, `search_index.py`, `evaluate_search.py`, `construct_benchmark_fixture.py`, `list_artefacts.py` |
| Session & Configuration | `session.py`, `config.py`, `workspace_registry.py`, `generate_key.py` |
| Lifecycle Management | `setup.py`, `configure.py`, `repair.py`, `upgrade.py`, `vault_registry.py`, `migrate_naming.py`, `migrations/` |
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
- `configure.py`
- `create.py`
- `edit.py`
- `evaluate_search.py`
- `fix_links.py`
- `list_artefacts.py`
- `migrate_naming.py`
- `_repair_runtime.py`
- `read.py`
- `repair.py`
- `rename.py`
- `search_index.py`
- `session.py`
- `setup.py`
- `shape_printable.py`
- `shape_presentation.py`
- `start_shaping_session.py`
- `start_shaping.py` (compatibility launcher)
- `sync_definitions.py`
- `upload_attachment.py`
- `workspace_registry.py`

### Standalone (no `_common` dependency)

- `generate_key.py` — stdlib only
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
| `_wikilinks.py` | Wikilink extraction, file index, broken link resolution, region-aware and table-aware text mutation | 15 |
| `_markdown.py` | Heading/callout parsing, shared structural target resolution, and typed literal-text regions (fenced code, inline code, HTML comments, `$$` math, raw HTML) | 28 |
| `_slugs.py` | Slug generation, validation, title/filename/slug conversions | 9 |
| `_search/lexical.py` | Lexical tokenisation and exact-anchor query detection | 2 |
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
