# `brain` CLI

A thin, optional dispatch CLI for Brain. Resolves the active vault, finds its central managed runtime ([DD-048](../architecture/decisions/dd-048-central-managed-runtime.md)), and exec's into a `.brain-core/scripts/<name>.py` script. The dispatch contract is [DD-049](../architecture/decisions/dd-049-brain-cli-thin-dispatch.md).

**Scripts in `.brain-core/scripts/` remain authoritative.** The CLI adds no new command semantics — it resolves the active vault/runtime and dispatches to the same top-level script entrypoints users can invoke directly. Users who never install the CLI lose nothing; everything still works by invoking scripts directly from a compatible Python 3.12+ launcher. See [Script Reference](scripts.md) for the canonical bootstrap / portable / managed command-family model; this page documents the optional `brain ...` shorthand only.

The command-interface migration now also carries an internal stdlib-only
`cli/launcher_catalogue.py` with the 23 pre-Brain or self-replacing operations
owned by the machine-global launcher. It records owner, version, entry point,
authority, effects, retry policy and explicit projection exclusions under
`brain.launcher-catalogue/1`. It does not change the v1 public grammar below;
the catalogue becomes active only in the coordinated CLI 2.0 cutover.

v0.54.35 adds the adjacent stdlib-only `cli/_launcher/` invocation boundary
and typed owners for registry default/list/resolve, CLI version and managed
runtime path/runnable resolution. These owners share the structural command
result vocabulary without importing selected-Brain `_application`, and they
remain internal until the same coordinated cutover.

v0.54.36 adds typed mutation owners for machine Brain registration, backfill,
unregistration, default selection/clearing and stale-entry pruning. They retain
the existing registry file semantics but expose exact no-op, dry-run, committed
and partial outcomes through command-specific launcher results and receipts.
Dry-run uses the same locked feasibility checks without writing. Registry-row and
default-pointer effects remain distinct, including when the first commits and
the second fails. The public v1 shell grammar is still unchanged.

v0.54.37 completes the effect-free launcher group with typed `brain.doctor` and
`operator.generate-key` owners. Canonical Doctor returns bounded CLI, derived
registry, runtime and optional vault findings; it detects `brains.json` drift
without repairing it and supplies canonical repair command IDs rather than
embedded shell strings. Operator-key output is a bounded tuple of typed
key/SHA-256 candidates. The existing v1 Doctor adapter keeps its current
behaviour until the coordinated cutover.

v0.54.38 adds typed `agent-skill.configure` ownership. Client and
configure/remove intent are closed request values; the target home directory is
trusted launcher context. A no-write pass validates every selected client before
application, dry-run returns that real plan, and committed adapter/backup effects
are receipted independently. The existing v1 configuration adapter stays public
until coordinated cutover.

v0.54.39 adds typed `machine.prune-runtimes` ownership. Canonical pruning uses
trusted current-Brain context and read-only registry comparison, requires a
successful live-process scan, returns a real no-write plan and receipts every
removed runtime directory. Recursive-deletion failure remains non-retryable and
outcome-unknown. The existing v1 machine adapter stays public until coordinated
cutover.

## Install

The CLI is installed automatically by `install.sh` to `~/.local/bin/brain` (user scope) or `/usr/local/bin/brain` (with `--system`). `upgrade.py` refreshes any installed CLI binary on each upgrade; it does not install a new CLI where none existed.

To skip the CLI: `bash install.sh --skip-cli <path>`.

To install only the CLI to an additional vault on a machine that already has it:

```bash
brain install <path>
```

`brain install` downloads the `install.sh` pinned to the Brain release ref bundled into the CLI binary, not a floating `main` branch script.

## Subcommands

### Dispatched (vault-scoped, run against `.brain-core/scripts/`)

| `brain` form | Dispatches to | Notes |
|---|---|---|
| `brain check [--actionable] [--severity S]` | `check.py` | Structural compliance check; same as `python3 check.py`. |
| `brain create --type T --title "Title" [...]` | `create.py` | Create a new artefact; accepts retry-safe `--body-handle`. |
| `brain edit edit\|append\|prepend\|replace_text\|delete_section [...]` | `edit.py` | Strict structural or exact-text edit. |
| `brain outline PATH` | `outline.py` | List exact editable heading/callout selectors. |
| `brain list [...]` | `list_artefacts.py` | Exhaustive filtered, paginated enumeration. |
| `brain search QUERY [...]` | `search_index.py` | Relevance-ranked search. |
| `brain stage --body\|--body-file ...` | `stage.py` | Create a retry-safe opaque body handle. |
| `brain discard-stage HANDLE` | `discard_stage.py` | Release an unused staged body immediately. |
| `brain upload-attachment --destination-key K --file P [--name N]` | `upload_attachment.py` | Add a non-markdown file beneath the artefact or standalone scope selected by `K`; base64 input is also supported. |
| `brain reparent PATH --parent P\|--clear` | `lifecycle.py reparent` | Change authoritative parent and derived paths. |
| `brain set-status PATH STATUS` | `lifecycle.py set-status` | Change status through its lifecycle handler. |
| `brain set-key PATH KEY` | `lifecycle.py set-key` | Change living key and derived ownership. |
| `brain set-naming-field PATH FIELD VALUE` | `lifecycle.py set-naming-field` | Change a naming-driving field safely. |
| `brain define {type\|trigger\|plugin} ...` | `define.py` | Guarded runtime-definition authoring; replacements use optimistic preconditions. |
| `brain rename "source" "dest"` | `rename.py` | Rename + update wikilinks. |
| `brain setup workspace [PATH] [...]` | `setup.py` | Bind a workspace to a Brain and converge the Brain-owned local scaffold. |
| `brain configure workspace {binding\|metadata\|bootstrap} [...]` | `configure.py` | Targeted workspace-owned configuration surfaces. |
| `brain configure mcp [...]` | `configure.py` | Explicit MCP transport configuration. |
| `brain configure agent-skills [--client claude\|codex\|all] [...]` | `configure.py` | Install, update, archive/replace, or remove active-Brain native-skill discovery adapters. |
| `brain configure semantic --enable [...]` | `configure.py` | Vault lifecycle configuration. |
| `brain repair {runtime\|mcp\|router\|lexical\|registry\|frontmatter\|semantic\|ownership}` | `repair.py` | Infrastructure and explicit metadata-authoritative ownership repair. |
| `brain upgrade --source P [...]` | `upgrade.py` | In-place brain-core upgrade. |
| `brain session [--json]` | `session.py` | Build the session bootstrap model. With no directly scoped vault, this command first resolves the target Brain through the machine-level resolution runtime, then dispatches to only that Brain's own `session.py`. |
| `brain read RESOURCE [--name N]` | `read.py` | Query compiled router resources; read failures use stderr and a non-zero exit. |
| `brain migrate-naming [--dry-run]` | `migrate_naming.py` | Filename migrations. |
| `brain fix-links [--fix]` | `fix_links.py` | Auto-repair broken wikilinks. |

Hyphens in subcommand names map to underscores in script filenames (`migrate-naming` ↔ `migrate_naming.py`). The legacy `brain init` dispatch noun has been retired; use `brain setup workspace` for workspace binding and `brain configure ...` for targeted workspace or MCP policy.

`brain session` has one narrow pre-dispatch exception, documented in [DD-054](../architecture/decisions/dd-054-machine-resolution-runtime.md). If `--vault` is present, the CLI dispatches directly to that Brain. If no vault is directly in scope, or a workspace is explicitly supplied through `BRAIN_WORKSPACE_DIR`, `--workspace-dir`, or the deprecated `--project-dir`, the CLI runs the stdlib-only machine resolver at `~/.brain/resolution-runtime/resolve_brain.py`. A local result then dispatches to the resolved Brain's own `session.py` with `--vault <target>`. A degraded result emits a `session_resolution` payload (`vault_root: null`) when `--json` is requested, or recovery guidance in text mode. Remote Brain targets are recognised as a future seam but return explicit "not yet supported" guidance for this non-MCP path.

### CLI-only (no script dispatch)

| Command | Purpose |
|---|---|
| `brain version`, `brain --version` | Print the CLI version. |
| `brain --help`, `brain -h` | List subcommands and resolution rules. |
| `brain install <path>` | Scaffold a new vault at `<path>` (wraps `install.sh`). Useful when adding a second vault. |
| `brain doctor [--json] [--actionable] [--severity S] [--vault V]` | Machine-level health checks. The shell still resolves the current/source Brain and keeps the degraded fallback, but when a source Brain is available it now hands the composed Doctor experience to `doctor.py`: CLI/PATH/Python basics, machine-level shared-runtime diagnosis from `doctor_machine.py`, and current-vault `check.py` as a separate vault-local section. |
| `brain machine <action> [...]` | Machine-level maintenance actions. The shell resolves a source Brain, then dispatches `machine.py` for explicit mutation surfaces such as legacy-Brain migration and orphan-runtime pruning. |

`brain doctor` bootstraps its Python handoff from the user-home vault registry (`vault_registry.py`, stored at `$XDG_CONFIG_HOME/brain/vaults`, default `~/.config/brain/vaults`). Once a source Brain is available, `doctor.py` becomes the launcher-safe composition owner for the Doctor experience: it renders CLI/PATH/Python basics, consumes machine-level shared-runtime findings from `doctor_machine.py` / `_machine/`, and runs the current vault's own `check.py --json` so the vault-local section stays owned by that Brain's version of `check.py`. The shell still prefers `vault_registry.py` as its curated bootstrap signal, but may fall back to `brains.json` when the curated registry no longer points at a runnable source Brain. Machine-level diagnosis now also points drifted Brains back to their own `repair.py mcp` / `repair.py registry` paths instead of treating that registration state as machine-owned. If `brain doctor` auto-repairs derived machine-registry drift, it exits non-zero once and expects a re-run to confirm the machine is clean.

`brain machine` shares that same source-Brain bootstrap and `_machine/` substrate, but exposes explicit mutation surfaces instead of diagnosis. The current actions are:

- `brain machine migrate-legacy [--brain SELECTOR] [--dry-run] [--json]` — converge discovered legacy Brains off vault-local `.venv` directories. Runtime, MCP, and registry repair stays Brain-owned: the machine layer delegates back to each target Brain's own `repair.py` scopes before removing the legacy `.venv`, then verifies the Brain now resolves to a shared central runtime.
- `brain machine prune-runtimes [--dry-run] [--json]` — remove shared central runtimes already proven orphaned by the canonical Brain/runtime registry plus live-process detection.

These four are the named exceptions to the "scripts authoritative" rule. Each operates *before or outside* any vault. See DD-049 §"Scripts stay authoritative".

## Vault resolution

For dispatched subcommands other than the `brain session` no-vault path, the CLI resolves the active vault in this order:

1. `--vault <path>` if present (absolute or relative — re-injected to the dispatched script as an absolute path).
2. `$BRAIN_VAULT_ROOT` env var.
3. CWD walk to the nearest `.brain-core/VERSION`.

If none resolves: `brain: no vault found — pass --vault <path>, set BRAIN_VAULT_ROOT, or run from inside a vault`.

## Argument forwarding

All arguments after the subcommand pass through to the dispatched script unchanged, *except* `--vault`, which the CLI consumes for its own resolution and re-injects as an absolute path. This guarantees scripts always see an absolute `--vault` even when the user gave a relative path or relied on CWD walk.

## Versioning

The CLI versions independently from `brain-core`. The CLI's contract is the dispatch surface plus CLI-only behaviour. A `brain-core` release that changes script behaviour does not affect CLI versioning. A `brain-core` release that *renames* a dispatched script requires a CLI major bump (or a back-compat shim), while backward-compatible CLI-only behaviour changes take a CLI patch bump. `brain install` is pinned separately through `BRAIN_INSTALL_REF`, which should always match the shipped `brain-core` release tag (`v<src/brain-core/VERSION>`).

Starting version: `1.0.0`. See DD-049 §"The dispatch surface is a versioned API".

## Examples

```bash
# Repair the managed runtime for the vault in the current directory.
brain repair runtime

# Run check.py against a specific vault.
brain check --vault ~/Documents/Brain --actionable

# Doctor mode, outside any vault — machine-level checks only.
brain doctor

# Doctor mode, inside a vault — machine diagnosis first, then the current vault's own check.py section.
cd ~/Documents/Brain && brain doctor

# Structured Doctor output for the current vault.
brain doctor --vault ~/Documents/Brain --json

# Preview orphan-runtime pruning without mutating anything.
brain machine prune-runtimes --dry-run

# Equivalent without the CLI (still supported, always).
python3 ~/Documents/Brain/.brain-core/scripts/repair.py runtime --vault ~/Documents/Brain
```
