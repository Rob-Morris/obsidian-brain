# DD-050: Unified closure-status vocabulary

**Status:** Implemented (v0.43.0)
**Extends:** DD-030

## Context

Every artefact type with a lifecycle had to invent its own closure vocabulary. By v0.42 the picture was:

- `living/design` carried four abandonment exits — `superseded`, `rejected`, plus `parked` and the success terminal `implemented`
- `living/release` used `cancelled` for abandonment, `shipped` for success
- `living/task` used `blocked` for "can't proceed right now", `done` for success
- `temporal/plan` had no abandonment terminal at all; in-the-wild plans were sitting at `superseded`, `rejected`, and `parked` outside the schema enum
- `living/documentation` already used `deprecated`
- Several types had `parked` but no terminal abandonment state

Two structural costs followed. First, the reason a closed artefact was closed was *partially redundant* with the status: designs already wrote `> [!info] Superseded by [[X]]` callouts, so the granular status mostly restated what the callout said. Second, every new artefact type had to redesign its closure semantics from scratch and pick names from a shifting precedent.

DD-030 froze the auto-move *mechanism* (terminal status → `+Status/` folder) but not the closure *vocabulary*. The vocabulary drift this DD addresses sat downstream of DD-030 and grew over time as new artefact types arrived.

## Decision

Adopt a uniform three-slot closure model across every artefact type with a lifecycle:

1. **Type-specific success terminal** — kept as-is (`implemented`, `completed`, `published`, `done`, `shipped`, `adopted`, `graduated`). These names carry domain meaning users already understand.
2. **`deprecated`** — single abandonment terminal across all types. The *reason* (superseded, rejected, cancelled, retired, duplicate) is captured in a `> [!info] Deprecated — <reason>` callout in the artefact body. The status enum stays small; the body carries the rich context.
3. **`parked`** — non-terminal pause, used only where pausing is meaningful. May resume.

Per-type schema changes ship under the same convention:

| Type | Retired status(es) | Replacement |
|---|---|---|
| `living/design` | `superseded`, `rejected` | `deprecated` |
| `temporal/plan` | `superseded`, `rejected` (de facto; not in prior enum) | `deprecated` (and `parked` added) |
| `living/release` | `cancelled` | `deprecated` |
| `living/task` | `blocked` | `parked` (non-terminal) + `deprecated` (terminal) |
| `living/idea`, `living/workspace`, `living/writing`, `living/journal`, `living/person`, `temporal/idea-log` | (no prior abandonment terminal) | `deprecated` added |
| `living/documentation` | (unchanged — already aligned) | — |

Folder convention follows status: `+Superseded/`, `+Rejected/`, and `+Cancelled/` retire; `+Deprecated/` replaces them. `+Implemented/`, `+Done/`, `+Shipped/`, etc. stay. `parked` artefacts stay in their parent folder (DD-030's "non-terminal status → leave in place" rule is preserved).

The auto-move mechanism in DD-030 is untouched; only the `terminal_statuses` lists per type change. The mechanism continues to derive the destination folder from the status name via `terminal_status_folder()`.

A versioned migration (`migrate_to_0_43_0.py`) rewrites retired statuses in existing vaults, prepends or rewrites deprecation callouts (preserving any existing supersession link), collapses retired folders into `+Deprecated/`, and removes empty retired folders. It is idempotent and runs on the next `upgrade.py`.

## Alternatives Considered

**Status alone retains the reason (status grows).** Keep `superseded`/`rejected`/`cancelled`/`blocked` and add their analogues to types that lacked them — every type gets its own four-or-five-exit abandonment vocabulary. Rejected: the callout already carries the reason for designs, so the granular status duplicates information. It also forces every new artefact type to redesign its closure vocabulary from scratch.

**Status is binary, reason lives in a separate field.** Two statuses (`active`, `closed`) with a `closure_kind` frontmatter field for granularity. Rejected: too coarse — losing the success/abandonment distinction in the status field would hide important semantic information and break the existing `terminal_statuses` → `+Status/` folder convention that DD-030 established.

**Add `deprecated_reason` as a structured frontmatter field instead of (or in addition to) a callout.** Useful if filtering by reason becomes load-bearing. Deferred: callouts are sufficient today, and we can add a structured field later without renaming statuses. The decision keeps that option open.

**Keep type-specific abandonment names but standardise them (e.g. `cancelled` for releases, `blocked` for tasks, `superseded` for designs).** Rejected: this preserves vocabulary fragmentation across types and gives no improvement for new types. The cost of remembering which name means abandonment for which type compounds.

## Consequences

- **Single closure vocabulary across the vault.** New artefact types pick up `deprecated`/`parked` and the matching folder convention for free.
- **Reason context lives in the body, not the enum.** Reads are richer (callouts carry prose) and the schema stays compact.
- **DD-030's auto-move contract is preserved.** Only the input (terminal_statuses list per type) changed; the mechanism, the env vars, and the `+Status/` folder shape are identical.
- **Breaking change for agents/scripts that hardcoded retired status names.** External tools referencing `superseded`/`rejected`/`cancelled`/`blocked` need to be updated. Minor version bump to v0.43.0.
- **Migration is reversible per artefact** — status field rewrites and folder moves can be undone manually if needed — but `migrate_to_0_43_0.py` does not provide a reverse command. Vault snapshots before upgrade are the recovery mechanism for unwanted bulk migrations.
- **Open extension point.** A `deprecated_reason:` frontmatter field can be added later without disturbing the status vocabulary. The callout convention provides the same information today in a form agents and humans can both read.
