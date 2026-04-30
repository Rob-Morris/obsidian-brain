# DD-045: MCP mutation surface split — `brain_move`, residual `brain_action`, scripts-only admin

**Status:** Implemented (v0.33.0)
**Extends:** DD-010, DD-025
**Extended by:** DD-046

## Context

The original mutation surface kept expanding inside `brain_action`. Over time it
mixed together:

- day-to-day content moves such as rename, convert, archive, and unarchive
- destructive deletion
- shaping workflows
- infrastructure and maintenance operations such as compile, build-index,
  definition sync, naming migration, and workspace registration

That shape created three problems.

First, the primary content-move operations were important enough that caller
ergonomics mattered. Agents and humans should be able to see a direct,
first-class contract for rename/convert/archive/unarchive rather than a large
`action + params` bucket.

Second, tightening the mega-tool schema led toward increasingly awkward
implementation workarounds. A single tool with many unrelated action shapes was
hard to describe cleanly in generated MCP metadata without private SDK hooks or
conditional-schema hacks.

Third, the product boundary itself had become muddy. Brain does not expose a
complete remote server-administration interface. Operational tasks such as
recompiling generated artefacts, syncing library definitions, or editing local
workspace bindings are fundamentally script/operator workflows, not day-to-day
agent mutation verbs.

## Decision

Split the mutation surface into a first-class move tool, a smaller residual
action tool, and scripts-only administration.

- **`brain_move`** is the primary MCP tool for artefact path/lifecycle
  transitions:
  - `rename`
  - `convert`
  - `archive`
  - `unarchive`
- `brain_move` uses a flat top-level contract for caller ergonomics, with
  runtime validation of op-specific field requirements instead of schema
  override tricks.
- **`brain_action`** remains as a smaller workflow/utility bucket for the
  residual mixed operations that are still useful through MCP but are not worth
  separate top-level tools:
  - `delete`
  - `shape-printable`
  - `shape-presentation`
  - `start-shaping`
  - `fix-links`
- `brain_action` keeps nested `params` shapes for discoverability, but the
  action-to-params pairing is validated at runtime rather than via a fully
  discriminated schema.
- Infrastructure and maintenance operations are **not** part of the MCP tool
  surface:
  - `compile`
  - `build_index`
  - `sync_definitions`
  - `migrate_naming`
  - `register_workspace`
  - `unregister_workspace`
- Those operational flows remain scripts-first, and the MCP server continues to
  be a thin wrapper over script-owned logic rather than a full remote admin API.

## Alternatives Considered

### 1. Keep one `brain_action` mega-tool and tighten its schema

Rejected. The surface stayed conceptually incoherent even if the schema became
stricter. It also pushed the implementation toward unsupported conditional
schema plumbing instead of a clearer product boundary.

### 2. Split every action into its own MCP tool

Rejected. That would maximise schema clarity, but it would grow the MCP surface
more than desired for the current product. The move family justified a dedicated
tool; the remaining utility actions did not.

### 3. Keep admin operations in MCP for parity with scripts

Rejected. The system does not aim to expose a complete remote admin interface.
Parity is achieved by keeping scripts as the source of truth and reusing script
logic where MCP does exist, not by forcing every operator workflow into the MCP
surface.

## Consequences

- The primary destructive content-move operations now have a direct, ergonomic,
  first-class contract through `brain_move`.
- `brain_action` becomes intentionally heterogeneous but smaller, so its simpler
  `action + params` contract is acceptable and easier to maintain.
- The MCP surface better reflects the product boundary: interactive artefact
  work is exposed; server administration and maintenance stay script-side.
- Historical decisions and docs that refer to archive/unarchive through
  `brain_action` are now superseded by this decision for current behavior.
