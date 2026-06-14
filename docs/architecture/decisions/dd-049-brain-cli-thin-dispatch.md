# DD-049: Brain CLI as thin dispatch layer

**Status:** Accepted (v0.38.0)
**Extends:** DD-003, DD-048
**Extended by:** DD-053, DD-054

## Context

DD-003 fixed the contract that any CLI must be a thin dispatcher: every vault operation lives as an importable function in `.brain-core/scripts/`, and CLI entry points call those functions without containing unique logic. That contract was written for an imagined CLI; until now Brain shipped no `brain` binary at all. Users invoked operations as `python3 .brain-core/scripts/<name>.py …` from inside each vault.

DD-048 then moved the managed Python runtime to a single machine-local location, content-addressed by `requirements.txt`. The runtime is no longer vault-resident. That change made a single global entry point natural: a process that resolves the active vault, finds its central venv, and execs the right script no longer has to live inside the vault.

A real `brain` CLI sits on top of DD-048 cleanly, but raises three questions DD-003 did not have to answer:

1. **What is the dispatch contract?** Which subcommand names map to which scripts, and is that mapping a versioned public surface or an implementation detail of the CLI binary?
2. **How does the CLI version relative to `brain-core`?** Vaults pin a specific `brain-core` version; the CLI binary is machine-global and serves multiple vaults at once. Coupling them undermines the point of a global entry point.
3. **Which subcommands are allowed to live in the CLI itself?** DD-003's "scripts authoritative" rule has to mean something — without a named, finite exception list, CLI-only logic will accrete.

DD-049 answers those three questions. It does not redo DD-003's reasoning; it makes DD-003 concrete for the CLI Brain is actually shipping.

## Decision

### The dispatch surface is a versioned API

The CLI's contract is the dispatch surface: subcommand names and the CLI-side argument shape (notably how `--vault` is handled). Dispatched subcommands in v1:

```
check        create       edit         rename
configure    repair       init         upgrade
session      read         migrate-naming   fix-links
```

Each maps 1:1 to `<vault>/.brain-core/scripts/<name>.py` (with `-` ↔ `_` translation in the filename). Adding a subcommand is a minor bump. Removing one, renaming a script the CLI dispatches to, or changing how an argument is forwarded is a major bump. Behaviour changes *inside* a script are not CLI-versioned at all — they ride with `brain-core`.

`BRAIN_CLI_VERSION` lives as a constant near the top of `cli/brain` and starts at `1.0.0`. The contract is a public API surface from day one; a `0.x` prefix would undermine the value of versioning the CLI separately at all.

### Scripts stay authoritative; four named CLI-only subcommands are the exceptions

Every dispatched subcommand is *exactly* `python3 .brain-core/scripts/<name>.py` under the hood, executed against the vault's central venv. The CLI adds no behaviour to those flows.

CLI-only subcommands are a finite, named set, justified by operating *before or outside* any vault:

- `brain version` / `brain --version` — prints `BRAIN_CLI_VERSION`. There is no vault to dispatch into.
- `brain --help` — lists dispatched + CLI-only surface.
- `brain install <path>` — wraps the installer to scaffold an additional vault on a machine that already has the CLI. No vault exists yet. The wrapped installer ref is pinned in the CLI binary; it is not a floating `main` branch download.
- `brain doctor` — machine-level checks. The shell layer keeps CLI/PATH/Python survivability checks; when a source Brain is available it hands richer shared-runtime topology checks to `doctor_machine.py` from that Brain's `.brain-core/scripts/`. When run inside a vault it *also* dispatches `check.py --json` and prints the combined output. If no source Brain can be found, the old shell-only `~/.brain/venvs/` summary remains the fallback.

Any future CLI-only subcommand for an operation that has vault context is a smell: fix the script and dispatch.

### `--vault` is the only argument the CLI consumes

All arguments after the subcommand pass through to the dispatched script unchanged, *except* `--vault`. The CLI:

1. Resolves the vault — explicit `--vault`, then `BRAIN_VAULT_ROOT` env, then CWD walk to the nearest `.brain-core/VERSION`.
2. Re-injects `--vault <absolute-resolved-path>` at the **front** of the forwarded argv (i.e. before the subcommand name).

This guarantees scripts always see an absolute vault path even when the user gave a relative `--vault` or relied on CWD walk. No script ever has to re-implement vault discovery to match the CLI.

**Contract for dispatched scripts:** every dispatched script MUST accept `--vault` at the top-level parser, not only on subcommand parsers. Because re-injection places `--vault <path>` before the subcommand name, argparse must see it at the top level or it treats the path as an unrecognised positional. Scripts that use subcommands (e.g. `configure.py`, `setup.py`) satisfy this requirement by passing `parents=[make_vault_parent_parser()]` from `_bootstrap.vaults` to both the top-level `ArgumentParser` and each leaf subparser. The shared parent uses `default=argparse.SUPPRESS` so that an absent `--vault` never clobbers a value already parsed at the top level.

### CLI source ships in the repo at `cli/brain`, never inside the vault

The CLI binary is machine-level chrome. It follows the `install.sh` pattern — top-level repo artefact, written to `~/.local/bin/brain` by the installer, refreshed by `upgrade.py` from `<--source-parent>/cli/brain`. It is never copied into `.brain-core/`. `.brain-core/` ships *runtime infrastructure the vault needs at every invocation* (e.g. `_common/_venv.py`); the CLI is the opposite — it resolves the vault before any vault code runs.

### Vault portability is preserved

`.brain-core/` stays per-vault. Centralising the runtime venv (DD-048) was the right move because the venv is sync-hostile machine state; `.brain-core/` is vault content and travels with the vault. A user with no CLI installed loses no functionality — the scripts are still directly invocable.

## Alternatives considered

### 1. Treat the dispatch contract as an implementation detail of the CLI binary

Rejected. If the dispatch list is not a named, versioned surface, every `brain-core` release that renames a script silently breaks the CLI for users on the previous CLI version, with no obvious signal. Declaring it an API forces the rename to be deliberate (CLI major bump or back-compat shim).

### 2. Couple CLI version to `brain-core` version

Rejected. The CLI is machine-global and serves multiple vaults that may sit on different `brain-core` releases. A coupled scheme would either require lockstep upgrade (defeating the point of the CLI) or be a fiction. Independent versioning is what makes the CLI a stable global entry point.

### 3. Let the CLI grow its own subcommands as needed

Rejected. DD-003 already settled this; DD-049 just names the exception list so it cannot grow by accretion. Four CLI-only subcommands, each justified by operating before or outside any vault. Anything beyond that is a script that has not been written yet.

### 4. Ship the CLI inside `.brain-core/`

Rejected. The CLI's job is to resolve the active vault and exec into its central venv — it must be able to run *outside* any vault. Shipping it per-vault would make `brain install <new-path>` chicken-and-egg, force every machine to designate one vault as the "CLI source", and conflict with the multi-vault premise that motivated the CLI in the first place.

### 5. Distribute via Homebrew / package manager

Deferred. Adds a dependency Brain currently does not require (DD-048 deliberately avoided package-manager assumptions for the same reason). A Homebrew formula is additive on top of this design and can land later; it does not change the contract.

## Consequences

- The dispatch list above is now a public API. Renaming a brain-core script the CLI dispatches to requires a CLI major bump or a back-compat shim. Adding a new dispatchable subcommand is a CLI minor bump.
- `brain-core` and `brain` (CLI) version independently. A user can upgrade `brain-core` in one vault without touching the machine CLI; the CLI continues to dispatch as long as the dispatch contract still holds.
- Tests verify the contract: every subcommand named in this DD must resolve to an existing `<scripts>/<name>.py` file in the working tree. Silent contract drift fails CI.
- `--vault` becomes the one canonical knob for vault selection across every dispatched subcommand. Scripts continue to accept `--vault` as they already do; the CLI just normalises the value before forwarding.
- DD-003's "thin dispatcher" promise is now testable and enforceable, not aspirational.
- A future `brain prune` subcommand (cleanup of unreferenced central venvs flagged as deferred work in DD-048) and `brain` as the home for additional global operations are pre-authorised under this contract: they will be dispatched subcommands backed by scripts, not CLI-only logic.
