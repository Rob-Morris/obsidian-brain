# DD-066: Domain language for launcher commands

**Status:** Implemented (v0.60.0)
**Extends:** DD-049, DD-061

## Context

DD-049 established the global `brain` launcher and DD-061 gave every command a
typed canonical owner. The first launcher catalogue nevertheless exposed some
implementation structure as user language: `machine.*` grouped unrelated
operations by where they execute, `brain.backfill` duplicated
`brain.register`, and two runtime reads forced callers to reconstruct one
diagnostic answer. The CLI also mechanically rendered `brain.*` IDs as
`brain brain ...`, even though the repeated noun conveyed no information.

Locality, ownership and dependency plane remain important catalogue metadata,
but they are poor command nouns. A user choosing an operation needs the noun to
name the domain being acted on.

## Decision

Launcher command IDs remain canonical `<noun>.<verb>` identifiers, but their
CLI entry points are owned explicitly rather than inferred mechanically.
`brain.*` launcher IDs collapse the redundant noun after the executable;
domain nouns remain visible.

The breaking catalogue change is:

- `machine.migrate-legacy` / `brain machine migrate-legacy` becomes
  `brain.migrate-legacy-installations` / `brain migrate-legacy-installations`;
- `machine.prune-runtimes` / `brain machine prune-runtimes` becomes
  `runtime.remove-orphans` / `brain runtime remove-orphans`;
- `brain.prune` / `brain prune` becomes `registry.remove-stale` /
  `brain registry remove-stale`;
- `brain.backfill` is removed because `brain.register` has the same user
  semantics; the internal helper remains available to lifecycle code;
- `runtime.resolve` and `runtime.resolve-runnable` become one
  `runtime.inspect` / `brain runtime inspect` command. Its typed result reports
  both the expected managed-runtime path and existence state, plus the selected
  runnable Python path and its closed source (`managed`, `legacy`, `launcher`
  or `unavailable`). Absence of a runnable is valid inspection state, not an
  error;
- `brain.resolve` / `brain resolve` remains unchanged: it resolves a Brain ID
  into its registered vault path.

No compatibility aliases are retained. The launcher catalogue falls from 24
to 22 commands. All affected commands are launcher-only, so the application
and MCP catalogues are unchanged. The breaking public CLI change advances the
independently versioned CLI to 3.0.0.

## Alternatives Considered

### Keep `machine` as a namespace

Rejected. It describes execution locality, not the resource or capability a
user is trying to operate.

### Preserve old spellings as aliases

Rejected. Agents adapt reliably to a coherent discovered interface; aliases
would enlarge the catalogue, weaken discovery and create two public names for
one owner.

### Keep runtime path reads separate

Rejected. Both reads have the same target, authority, effect, dependency and
failure semantics. A single inspection result is more complete and avoids a
second call without introducing mutation or an unrelated option union.

### Rename `brain resolve`

Rejected. `resolve` directly expresses the stable Brain-ID-to-vault-path
transformation and is clearer than alternatives such as `locate` or
`get-path`.

## Consequences

Launcher commands now advertise the acted-on domain, machine-global ownership
stays explicit metadata, and runtime diagnosis requires one call. Existing
automation must adopt the new CLI 3 spellings; discovery and documentation
provide the replacements. Upgrade installs the new versioned CLI distribution
as part of the normal checked cutover. MCP clients see no tool change.

## Implementation Notes

The internal `_launcher.machine` module may continue to own machine-global
implementation seams. Internal package placement is not a public command
namespace. Contract tests pin the 22-entry launcher catalogue, the absence of
the removed IDs and aliases, the unified runtime result, and unchanged MCP
projection eligibility.
