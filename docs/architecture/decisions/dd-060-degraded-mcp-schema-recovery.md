# DD-060: Preserve recovery guidance when MCP clients degrade schemas

**Status:** Implemented (v0.54.0)
**Extends:** DD-044

## Context

DD-044 made generated JSON Schema the canonical delivery surface for MCP
parameter semantics because clients may truncate long tool descriptions. That
remains the correct default, but a second real client failure mode has now been
observed: a valid nested discriminated schema can be reduced to opaque
`unknown` variants in the agent-facing declaration even though the raw MCP
schema retains its properties, discriminators, and descriptions.

`brain_create`, `brain_edit`, `brain_define`, and `brain_action` all exposed
this gap in one supported client. Their raw schemas retained exact variants,
while the agent-facing declarations reduced their required request objects to
opaque `unknown` types. In the observed failure, an agent guessed
`brain_create.request.body.kind`; strict boundary validation prevented a
mutation, but its first error only reported an extra `body` field and did not
name the valid replacement.

## Decision

Brain keeps generated schema as the canonical contract and adds two narrow
recovery mechanisms for observed degradation:

- A tool-level summary may carry one compact canonical request fragment when a
  real client has hidden a load-bearing nested shape. This is a compatibility
  fallback, not a parameter manual, and remains subject to the existing summary
  length budget.
- Boundary validation for recognised legacy-looking mistakes names the invalid
  and replacement fields in one error. It does not accept or silently translate
  aliases.

Each affected tool summary exposes one common canonical request fragment.
For `brain_create`, pre-validation also explains that `body` must become
`content` and `kind` must become `source` before normal strict schema validation
proceeds.

## Alternatives Considered

### 1. Rely exclusively on the raw JSON Schema

Rejected for affected clients. The server publishes the right schema, but the
agent cannot act on metadata its client does not render.

### 2. Restore the old flat mutation parameters

Rejected. Flattening would weaken the resource-specific boundary introduced in
v0.52.0 and make invalid cross-product combinations less discoverable in
clients that do preserve the schema.

### 3. Accept `body` and `kind` as compatibility aliases

Rejected. Silent aliases would create two public contracts, preserve stale
calling patterns indefinitely, and make later schema cleanup harder. Precise
errors provide recovery without widening the interface.

### 4. Put the complete request contract in the tool description

Rejected. That recreates the truncation and duplication problems DD-044 was
designed to prevent. Only the smallest fragment required to recover is copied.

## Consequences

- Agents using degraded client declarations can form one common request for
  each affected tool without source inspection.
- Wrong `body` or `kind` guesses fail before mutation with a complete correction
  instead of a generic extra-field or missing-discriminator error.
- The discriminated request shapes remain strict for conforming clients.
- Summary fallbacks remain limited to tools whose schemas were observed to
  degrade in a supported client, rather than becoming speculative duplication
  across the flat tool surface.
