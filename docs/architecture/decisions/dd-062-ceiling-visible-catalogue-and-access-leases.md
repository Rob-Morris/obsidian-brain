# DD-062: Ceiling-visible catalogue with independent access leases

**Status:** Implemented (v0.57.0)
**Extends:** DD-033, DD-044, DD-061

## Context

The granular command architecture makes profile authority understandable, but an authenticated profile still serves two different purposes: the maximum authority granted to an identity and the authority needed for the agent's current task. Giving every session its full profile by default broadens routine work. Mutating the MCP tool catalogue whenever authority changes would conflate security with schema loading and depend on client behaviour that is not portable: Claude Code supports deferred definitions and live tool-list refresh, while current Codex clients require reconnect for newly introduced definitions.

Brain therefore needs a small elevation model that remains correct with cached tool definitions, preserves the typed application boundary, works in bootstrap and low-dependency planes, and distinguishes convenience from a genuine user-controlled approval boundary.

## Decision

Authentication establishes a principal and immutable profile ceiling before MCP tool registration. The server and proxy header expose a deterministic ceiling-visible catalogue; `command.list` and `command.describe` enforce the same ceiling. Above-ceiling names and schemas are omitted. Credential or ceiling changes require proxy replacement and client re-discovery.

Active authority is separate. It starts from `defaults.access.initial_profile`, Reader by default, intersected with the ceiling. `access.status`, `access.request` and `access.reduce` are bootstrap-tier application commands available across MCP, CLI, direct script and typed Python. A request covers one exact command or a small sorted set. A lease is principal-scoped, has an absolute expiry, may have a bounded use count and is enforced on every invocation. Uses are consumed only after semantic request and capability preflight. Active-grant changes never mutate `tools/list`.

`vault.access.elevation_policy` selects:

- `automatic`: issue the lease immediately and record intent; this is an audit and context-minimisation mechanism, not a defence against the authenticated agent;
- `external`: create a pending request; a different, separately trusted registered operator approves through CLI-only launcher command `access.approve`, and the approver profile must cover every requested command;
- `denied`: refuse elevation.

External approval is not an application or MCP command. Its secret is trusted adapter context, never a semantic field or receipt value. Schema disappearance, notification delivery and lease-file cleanup are advisory only; call-time enforcement is authoritative.

## Alternatives Considered

### Expose only the current active grant and send `tools/list_changed`

Rejected as the portable baseline. It couples authorisation to client cache behaviour, and current supported clients do not provide equivalent hot-refresh guarantees. Revoked cached definitions would still require call-time enforcement.

### Expose the full administrator catalogue to every principal

Rejected. It leaks unavailable names and schemas, enlarges discovery and weakens the meaning of authenticated profiles. Client-native lazy loading can reduce prompt cost without discarding ceiling projection.

### Treat every elevation as human security approval

Rejected. Many users want Reader-default task focus under an already trusted profile without repeated prompts. Automatic and external policies make the distinction explicit instead of pretending an agent-approved lease is a security barrier.

### Keep elevation state in an MCP connection

Rejected. Connections are transient, remote requests may be sessionless, and authority must survive proxy child replacement. State is principal-scoped under the selected Brain and bounded by absolute expiry.

## Consequences

- Routine agents start with a compact Reader grant while retaining discoverable tools up to their authenticated ceiling.
- Claude and Codex can use their own lazy-loading policies; Brain correctness does not depend on either client loading or refreshing schemas in a particular way.
- External human confirmation has a real non-MCP path, while automatic elevation remains honest about its limits.
- Catalogue/profile counts differ: the application owns 74 commands, 63 are MCP-eligible, and each server exposes only its ceiling subset.
- Custom profiles are not silently widened. Exact shipped profiles migrate to include the three access controls.
