# DD-059: Narrow attachment upload boundary

**Status:** Implemented (v0.53.4)
**Extends:** DD-031, DD-045

## Context

Brain treats `_Assets/` as infrastructure and blocks it from ordinary agent
writes under the path security model in DD-031. That is correct for arbitrary
paths and generated output, but it left no supported way for an agent limited to
Brain scripts, the `brain` CLI, or MCP to add an Obsidian attachment. Such an
agent had to obtain separate filesystem write access to the vault even though
`_Assets/Attachments/` is the documented destination for user-added files.

Attachments are also not artefacts. They have no Brain type, frontmatter,
lifecycle, or retrieval-index entry, so forcing them through `brain_create`
would blur the artefact model and complicate its resource-discriminated schema.

## Decision

Add a first-class, script-owned attachment operation whose required
`destination_key` selects one validated scope beneath `_Assets/Attachments/`.

- `upload_attachment.py` owns filename validation, base64 decoding, the 16 MiB
  size bound, atomic byte writes, collision behaviour, and result metadata.
- `brain upload-attachment` dispatches to that script. It accepts either a
  caller-owned source file or base64 bytes, so the caller does not need write
  access to the vault.
- `brain_upload_attachment(destination_key, name, content_base64)` exposes the
  same operation through MCP for contributor and operator profiles.
- A canonical living artefact key in `type/key` or `type~key` form must resolve
  through the active compiled artefact index. Brain normalises it to
  `_Assets/Attachments/type~key/<filename>`.
- A bare validated key selects a standalone reusable attachment folder at
  `_Assets/Attachments/<key>/<filename>`. This covers temporal artefacts and
  assets that do not belong to one living artefact.
- A destination path is never accepted. Slash and tilde forms are reserved for
  canonical living artefact resolution; arbitrary nested paths and unknown
  artefact keys fail. There is no flat fallback.
- The final destination is always a single filename within the derived scope.
  Caller-supplied paths, dot-prefixed names, markdown files, control characters,
  Windows-reserved names, trailing periods, and characters that are invalid on
  Windows or make the returned Obsidian embed ambiguous are rejected.
- An identical retry returns the existing attachment with `created: false`.
  Different bytes at the same filename fail rather than overwrite.
- Results include resolved destination kind/key/folder, the vault-relative
  path, an Obsidian embed, byte count, SHA-256 digest, and whether a file was
  created.

Living identity changes carry the attachment scope with the artefact. A key
change or living-to-living conversion moves the complete derived scope and
rewrites explicit Obsidian embeds. The operation fails before metadata writes
if the new scope already exists; scopes are never merged. Filename-only moves,
status changes, reparenting, archive, and unarchive leave the scope unchanged.
Living-to-temporal conversion and deletion preserve the old scope and report it
as orphaned so callers can decide whether to keep or remove it.

The general underscore-folder write guard remains unchanged. `_Assets/` is not
added to `_WRITE_ALLOWED_UNDERSCORE`; this operation is a narrow capability
whose destination is constructed by Brain rather than accepted from the caller.
The fixed namespace, derived scope, intermediate folders, and final component
must not be symlinks. Bounds resolution, the shared atomic-write kernel, and the
vault mutation lock still apply.

## Alternatives Considered

### 1. Add `_Assets` to the general write allowlist

Rejected. That would permit ordinary create/edit flows to write anywhere under
attachments and generated output, weakening a default-deny boundary far beyond
the required capability.

### 2. Add `attachment` as a `brain_create` resource variant

Rejected. Attachments do not participate in the artefact/config resource model,
and binary base64 content would make the existing markdown-oriented content
variants harder to understand.

### 3. Extend body staging to arbitrary binary payloads

Rejected for this slice. Retry-safe binary staging could be useful for larger
future transfers, but it is not required to close the attachment-access gap and
would broaden staging lifecycle and quota semantics.

### 4. Allow overwriting an existing attachment

Rejected. Additive creation is safe to retry and suitable for normal approval
policies. Replacement is destructive and should be designed as an explicit
operation if a real workflow requires it.

## Consequences

- Agents can add images, PDFs, SVGs, and other non-markdown attachments without
  direct vault filesystem access.
- MCP payloads pay base64's transport overhead and have a 16 MiB decoded limit.
- Correcting an existing attachment with different bytes still requires a
  future replacement operation or explicit filesystem action.
- Existing flat files under `_Assets/Attachments/` are not migrated
  automatically.
- Lifecycle operations preserve potentially still-useful attachment data on
  delete or conversion to temporal and surface the orphaned scope explicitly.
- General writes to `_Assets/` remain blocked, including writes to
  `_Assets/Generated/`.
