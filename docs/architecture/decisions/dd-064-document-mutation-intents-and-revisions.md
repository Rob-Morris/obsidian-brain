# DD-064: Document mutations use intent-specific commands and exact revisions

**Status:** Implemented (v0.59.0)

## Context

The original `document.edit` combined whole-body writes, literal replacement,
frontmatter mutation, and Markdown-structure editing behind one change union.
Although typed, that surface made unrelated invariants travel together and made
literal replacement resemble semantic editing. It also allowed a document to
change between an agent's read and write without the caller noticing.

## Decision

Brain owns four cohesive document mutation commands across artefacts, memories,
skills, styles, and templates:

- `document.write` replaces, appends, or prepends the complete Markdown body;
- `document.patch` replaces literal text using `unique`, `occurrence`, or `all`
  match policy and has no structural scope;
- `document.edit` replaces, inserts into, or deletes typed heading, callout, or
  document-intro structures;
- `document.update-frontmatter` sets or removes metadata fields without a body
  operation.

Each command receives the same `document` locator and a mandatory
`expected_revision`. Editable `artefact.read` and `resource.read` results return
that revision. The token is a SHA-256 digest of the exact persisted bytes,
including frontmatter. Mutation compares it while holding the vault mutation
lock, immediately before effects, and never retries automatically. A mismatch
returns `conflict`, consumes no staged content, and provides the appropriate
read command as its next action. Successful results return the new revision.

The public structural interface names Markdown concepts directly. Raw internal
`target`, `scope`, and selector spellings remain an implementation detail of the
existing document engine.

## Consequences

The four names make intent, authority, schema, and recovery independently
discoverable without returning to target-specific command proliferation. Agents
can replace ordinary filesystem editing with a coherent read–mutate loop while
retaining a safe raw-body escape hatch. Structural editing costs a larger schema
than the other three commands, but that cost represents useful semantic choices
rather than an opaque mini-language.

Older broad document-mutation grants project once to all four commands. No old
invocation shape is translated at runtime because a mixed body/frontmatter edit
cannot be mapped without guessing intent.

## Alternatives rejected

- One broad `document.edit` union: couples independent policies and obscures the
  difference between textual and semantic mutation.
- Target-specific commands per resource: repeats identical semantics and grows
  the catalogue without adding intent.
- Optional revisions: callers would silently opt out of lost-update protection.
- Filesystem timestamps or router versions: neither identifies the exact bytes
  the caller read.
