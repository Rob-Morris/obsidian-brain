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
| `brain configure semantic --enable [...]` | `configure.py` | Vault lifecycle configuration. |
| `brain repair {runtime\|mcp\|router\|lexical\|registry\|frontmatter\|semantic}` | `repair.py` | Infrastructure repair. |
| `brain init [--client {claude,codex,all}] [...]` | `init.py` | MCP registration. |
| `brain upgrade --source P [...]` | `upgrade.py` | In-place brain-core upgrade. |
| `brain session [--json]` | `session.py` | Build the session bootstrap model. |
| `brain read RESOURCE [--name N]` | `read.py` | Query compiled router resources. |
| `brain migrate-naming [--dry-run]` | `migrate_naming.py` | Filename migrations. |
| `brain fix-links [--fix]` | `fix_links.py` | Auto-repair broken wikilinks. |

Hyphens in subcommand names map to underscores in script filenames (`migrate-naming` ↔ `migrate_naming.py`).

### CLI-only (no script dispatch)

| Command | Purpose |
|---|---|
| `brain version`, `brain --version` | Print the CLI version. |
| `brain --help`, `brain -h` | List subcommands and resolution rules. |
| `brain install <path>` | Scaffold a new vault at `<path>` (wraps `install.sh`). Useful when adding a second vault. |
| `brain doctor` | Machine-level health checks (PATH, `~/.brain/venvs/`, CLI version). Inside a vault, also dispatches `check.py`. |

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

# Doctor mode, inside a vault — machine checks + dispatched check.py.
cd ~/Documents/Brain && brain doctor

# Equivalent without the CLI (still supported, always).
python3 ~/Documents/Brain/.brain-core/scripts/repair.py runtime --vault ~/Documents/Brain
```
