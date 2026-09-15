# DD-076: Session-first routing with a maintained authored fallback

**Status:** Implemented (v0.68.6)
**Extends:** DD-038, DD-065

## Context

DD-038 established one canonical session model with JSON and generated Markdown
renderers. DD-065 made the raw-file path correct and testable, but described it
as legacy while saying normal operation used scripts or CLI with optional MCP.
That route classification no longer matches the product or the shipped command
architecture: MCP `session.start` is the normal agent bootstrap, and the CLI and
direct-script projections are alternatives when their runtimes are available.

The generated `.brain/local/session.md` cannot be the last-resort path. A truly
naive vault-local agent may be able to read files but unable to use MCP, launch
the CLI, run Python, compile configuration or rely on any generated assets. A
template-vault-derived vault with copied `.brain-core/` instructions must remain
enough for that agent to discover the authored router, taxonomy and owner rules.

The fallback's use in the wild is unknown. It is not the primary operating
model, but its authored instructions are cheap to maintain when isolated from
the normal session payload. Retiring it without evidence would remove a useful
capability without reducing meaningful runtime cost.

## Decision

`session.start` owns the canonical runtime bootstrap. Agents select the first
route they can use:

1. MCP `session_start` is the normal entry point.
2. `brain session start --json` invokes the same selected-Brain owner through
   the launcher.
3. Supported direct scripts provide a tool-backed projection when the calling
   interpreter meets the command's dependency tier.
4. Without usable tools, `.brain/local/session.md`, when present, is a generated
   Markdown projection of the canonical session model.
5. Without tools or generated assets, `.brain-core/md-bootstrap.md` routes to
   copied core instructions and authored vault configuration. It must require no
   code execution or compilation.

Every tool-backed `session.start` continuation completes before ordinary work.
The router and taxonomy are authoritative configuration inputs, not the routine
bootstrap surface. The generated mirror is derived state, not a second source of
truth.

The authored Markdown route remains a maintained, vault-local edge-case
fallback. It is correctness-scoped and isolated from the normal payload, as
DD-065 decided, but it is not deprecated. Deprecation or removal requires a
separate decision grounded in evidence about consumers and maintenance cost.

Repository contributor bootstrap remains a separate concern. `AGENTS.md` and
the contributor documentation may impose repository workflow without placing
that policy in installed Brain runtime instructions.

## Alternatives Considered

**Treat the generated mirror as the universal non-tool fallback.** Rejected
because the mirror is derived state and may not exist in a copied or never-run
vault. It cannot satisfy the no-generated-assets case.

**Retire the authored fallback now.** Rejected because actual usage is unknown,
the isolated path has low maintenance and runtime cost, and removal would create
a silent capability gap for vault-local agents that can only read files.

**Present authored Markdown as a peer operating model.** Rejected because it
would obscure the session-first product, duplicate tool capabilities in prose
and encourage drift. The fallback documents the minimum contracts needed to
work safely; tools remain the normal route.

**Rewrite DD-065's historical premise.** Rejected because decision bodies record
the reasoning that applied when they were made. This decision extends DD-065 and
the predecessor gains only a navigational pointer.

## Consequences

Current public and shipped documentation names one route order and one canonical
session owner. Contract tests check ordered route summaries and the fallback's
required authored inputs.

`.brain-core/index.md` stays a thin route selector. `.brain-core/md-bootstrap.md`
contains only the no-code reading path and sends agents back to the index if tools
become available. `session-core.md` remains unchanged, so maintaining the edge
case adds no canonical session-payload cost.

Future changes to route precedence, generated-mirror ownership or fallback
lifecycle extend or supersede this decision and update the ordered documentation
contract together.
