# `brain` CLI

A thin, optional dispatch CLI for Brain. Resolves the active vault, finds its central managed runtime ([DD-048](../architecture/decisions/dd-048-central-managed-runtime.md)), and exec's into a `.brain-core/scripts/<name>.py` script. The dispatch contract is [DD-049](../architecture/decisions/dd-049-brain-cli-thin-dispatch.md).

**Scripts in `.brain-core/scripts/` remain authoritative.** The CLI adds no new command semantics — it resolves the active vault/runtime and dispatches to the same top-level script entrypoints users can invoke directly. Users who never install the CLI lose nothing; everything still works by invoking scripts directly from a compatible Python 3.12+ launcher. See [Script Reference](scripts.md) for the canonical bootstrap / portable / managed command-family model; this page documents the optional `brain ...` shorthand only.

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
| `brain create --type T --title "Title" [...]` | `create.py` | Create a new artefact. |
| `brain edit edit\|append\|prepend\|delete_section [...]` | `edit.py` | Edit existing artefacts. |
| `brain rename "source" "dest"` | `rename.py` | Rename + update wikilinks. |
| `brain setup workspace [PATH] [...]` | `setup.py` | Bind a workspace to a Brain and converge the Brain-owned local scaffold. |
| `brain configure workspace {binding\|metadata\|bootstrap} [...]` | `configure.py` | Targeted workspace-owned configuration surfaces. |
| `brain configure mcp [...]` | `configure.py` | Explicit MCP transport configuration. |
| `brain configure semantic --enable [...]` | `configure.py` | Vault lifecycle configuration. |
| `brain repair {runtime\|mcp\|router\|lexical\|registry\|frontmatter\|semantic}` | `repair.py` | Infrastructure repair. |
| `brain upgrade --source P [...]` | `upgrade.py` | In-place brain-core upgrade. |
| `brain session [--json]` | `session.py` | Build the session bootstrap model. |
| `brain read RESOURCE [--name N]` | `read.py` | Query compiled router resources. |
| `brain migrate-naming [--dry-run]` | `migrate_naming.py` | Filename migrations. |
| `brain fix-links [--fix]` | `fix_links.py` | Auto-repair broken wikilinks. |

Hyphens in subcommand names map to underscores in script filenames (`migrate-naming` ↔ `migrate_naming.py`). `brain init` remains dispatchable as a hidden compatibility shim, but it is no longer a taught first-class public noun for workspace setup or MCP policy.

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

For dispatched subcommands the CLI resolves the active vault in this order:

1. `--vault <path>` if present (absolute or relative — re-injected to the dispatched script as an absolute path).
2. `$BRAIN_VAULT_ROOT` env var.
3. CWD walk to the nearest `.brain-core/VERSION`.

If none resolves: `brain: no vault found — pass --vault <path>, set BRAIN_VAULT_ROOT, or run from inside a vault`.

## Argument forwarding

All arguments after the subcommand pass through to the dispatched script unchanged, *except* `--vault`, which the CLI consumes for its own resolution and re-injects as an absolute path. This guarantees scripts always see an absolute `--vault` even when the user gave a relative path or relied on CWD walk.

## Versioning

The CLI versions independently from `brain-core`. The CLI's contract is the dispatch surface (subcommand names + argument shape). A `brain-core` release that changes script behaviour does not affect CLI versioning. A `brain-core` release that *renames* a dispatched script requires a CLI major bump (or a back-compat shim).

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
