# DD-043: Bootstrap launchers and managed runtimes

**Status:** Implemented (v0.32.6)
**Extends:** DD-023, DD-039, DD-036

## Context

Brain's lifecycle tooling has two competing pressures:

- User-facing install, upgrade, init, and repair flows must still run when the vault's local runtime is missing or broken. `install.sh` is a shell bootstrap that may run before Brain is installed in the target vault at all; the Python lifecycle CLIs need a compatible launcher in the same situations.
- Packageful Brain work should not silently drift onto a user's wider Python environment, because that muddies support, hides state, and risks polluting non-vault interpreters.

The new `repair.py` flow sharpens that tension. Its primary job is to recover exactly the cases where the managed runtime is unavailable, but the repaired steady state must still be the vault-local runtime used by MCP and by packageful maintenance flows.

Earlier decisions already point in this direction:

- [DD-023](dd-023-init-script.md) made the lifecycle scripts self-contained when they need to operate before the normal runtime is available.
- [DD-039](dd-039-multi-client-mcp-install-scopes.md) made `init.py` the MCP registration owner across clients and scopes.
- [DD-036](dd-036-safe-write-pattern.md) established that bootstrap paths may duplicate small self-contained primitives when importing shared runtime helpers would be unsafe.

What was missing was a single runtime principle spanning install, upgrade, init, and repair.

## Decision

Brain distinguishes between **bootstrap launchers** and **managed runtimes**.

- Brain bootstrap flows may start either from the shell installer (`install.sh`) or, for Python lifecycle CLIs, from a compatible native Python 3.12+ interpreter when the managed runtime is missing or broken.
- The vault-local `.venv` is the canonical managed runtime for packageful Brain execution.
- Automatic dependency installation targets the vault-local `.venv`, never the user's wider Python environment.
- Each repair scope declares its own dependency requirements. Only scopes that need third-party packages (currently `mcp`) sync `.brain-core/brain_mcp/requirements.txt`; stdlib-only scopes (`router`, `index`, `registry`) require a compatible managed-runtime interpreter but skip the package sync, so they remain usable in offline or restricted environments.
- Successful packageful recovery converges execution back into the vault-local `.venv`; a global/native interpreter is a launcher of last resort, not the intended steady state.

`repair.py` is the first explicit CLI surface built around that split:

- its bootstrap path stays dependency-light and may run from an external launcher
- it repairs or creates the vault-local `.venv` when needed
- once the managed runtime is healthy enough, it hands off into that `.venv`
- the named repair scopes then run inside the managed runtime

The same principle constrains future lifecycle work:

- user-facing bootstrap scripts may stay self-contained where necessary
- but packageful work should either run in, or hand off into, the vault-local managed runtime
- no script should silently normalise Brain onto a random global interpreter and treat that as a repaired success state

## Alternatives Considered

### 1. Use any compatible interpreter as both launcher and steady state

Rejected. It is convenient in the short term, but it turns support into guesswork: MCP may run from one machine's global Python, upgrade from another, and repair from a third. That makes dependency ownership and failure diagnosis much harder.

### 2. Require the vault-local `.venv` for every lifecycle entry point

Rejected. This fails exactly when repair is most needed: the managed runtime may be missing or broken. Brain needs an external bootstrap path for recovery.

### 3. Keep the distinction only inside `repair.py`

Rejected. The launcher-vs-managed-runtime split is an operating-model decision, not just a repair implementation detail. Install, upgrade, init, and future doctor-style tooling all need the same rule.

## Consequences

- Brain now has a clearer, safer runtime story: bootstrap may start outside the vault runtime, but packageful steady state lives inside the vault.
- `repair.py` can recover a broken `.venv` without installing packages into the user's main environment.
- Future lifecycle work should expose the same boundary explicitly rather than relying on fallback magic.
- Existing scripts that still tolerate broader launcher choices should be evaluated against this principle over time; the decision establishes the target operating model even where the entire script family has not yet been tightened to the same degree.
