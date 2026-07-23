# DD-056: Lifecycle metadata is authoritative and tool mutations use explicit handlers

**Status:** Implemented (v0.52.0)
**Extends:** DD-030, DD-041, DD-050
**Extended by:** DD-057

## Context

Living artefact metadata such as `parent`, `key`, and `status` determines more
than one YAML value. It also projects into owner/status folders, tags,
descendant references, filenames, and wikilinks. Generic `brain_edit`
frontmatter changes could therefore leave a partially updated vault. At the
same time, Brain is an Obsidian vault: humans and other applications may edit
the YAML directly, so tool-level prevention cannot be the only integrity
mechanism.

## Decision

The lifecycle field is the source of truth. Brain's CLI and MCP surfaces reject
generic edits of fields that have invariant handlers and identify the dedicated
command: reparent, set status, set key, or set naming field. Those commands own
the complete derived change set and run under the shared vault mutation lock.
Before writing, a lifecycle command renders the complete candidate naming state
and validates required placeholder values and declared regexes. An invalid
status or naming-field transition therefore fails atomically rather than
persisting metadata whose filename projection cannot be produced.

Direct filesystem/Obsidian edits remain possible. Doctor detects disagreement
between valid authoritative metadata and its filesystem projection. Ownership
repair previews and then moves files, descendants, and links toward the
metadata. It never infers a missing authoritative parent from the folder
structure.

## Alternatives Considered

### Treat folder structure as authoritative

Rejected. The current structure does not encode every lifecycle state and
would require a deeper migration, including representing all statuses as
folders. Keeping both metadata and structure independently authoritative would
also make conflicts irresolvable.

### Permit generic metadata edits and reconcile later

Rejected for Brain-owned tools because they already know a handler exists and
can preserve invariants synchronously. Delayed reconciliation remains the
necessary recovery path for external editors.

## Consequences

- Tool callers receive an immediate, actionable error instead of creating
  known drift.
- External editing remains compatible with Obsidian's filesystem-first model.
- Doctor and repair form the explicit detection/recovery path for out-of-band
  drift.
- Metadata schema changes that acquire derived behaviour must add a dedicated
  handler before generic editing is prohibited.
- Naming contracts are validated at compile time: custom placeholders require
  explicit backing-field declarations, including in simple naming form.
