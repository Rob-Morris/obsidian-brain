# DD-078: Registration-driven MCP lifecycle and a stable user bootstrap

**Status:** Accepted
**Extends:** DD-043, DD-051, DD-052, DD-077

## Context

User MCP entries outlive individual Brains and dependency environments. Previously
they were recorded in one vault's installation history and launched a rotating
managed Python. Upgrade and repair inspected primarily vault-self project files,
so a successful Core upgrade could leave the shared entry pointing at an obsolete
environment. A stopped client was also absent from live-process runtime retention.

DD-051 and DD-052 already separate workspace binding from transport and define
fail-closed target resolution. DD-043 separates the bootstrap interpreter from
managed dependencies; DD-077 makes the dependency contract content-addressed.
The missing boundary is the lifetime and ownership of the shared connection.

## Decision

Canonical registration intent, exact last-applied ownership evidence, and observed
native configuration are distinct facts. New setup requires explicit client
selection, including `all`. Repair restores missing admitted projections but
never interprets a workspace registration or a discovered client file as new
installation intent.

Ownership is not ownership of client policy: Codex server-default/tool approval
settings and tool allow/deny lists are preserved independently of its transport
claim. Claude's permission/approval settings and Grok's sibling permission/UI
tables remain client-owned. Migration, configure, repair and inspection use the
same transport comparison without accepting changed routing, environment or
unknown server options. Explicit removal of a server also removes its nested
policy, but never sibling/global client permission settings.

User registration belongs to one OS user, in the trusted Brain config-home
ledger. Project/local registration remains in the owning Brain's local ledger.
Native scopes remain client-specific. Configuration destinations are derived
from trusted layout, not arbitrary paths supplied by a ledger.

The generic user command is the absolute installed `brain mcp serve` bootloader.
The checked CLI installation records an absolute, validated base Python outside
rotating Brain runtimes. Startup does not discover Python through PATH, provision
packages, adopt registrations, or change bindings. It isolates ambient Python
imports and development overrides, resolves the target through the existing
ladder, then uses that Core's runtime contract and proxy. A broken installation
has an explicit installer recovery path; base-Python relocation does not require
rewriting each client entry.

Shared planning leaves compose three repair breadths: workspace projections;
one Brain's runtime, vault-self and registered integration targets; all registered
local Brains plus shared machine state. Runtime environments and configuration
destinations are deduplicated. Remote registry entries confer no local write
authority. Damaged, unavailable or conflicting coverage is an error, not an empty
workset. Cross-root composition belongs to the machine launcher, not vault-local
application repair.

Legacy recognition is a bounded migration/admission responsibility. Exact surviving
claims can recover reverse registration and transfer user ownership; ambiguous
claims and modified entries require explicit resolution. Before/after migration
evidence is durable and resumable. Normal repair accepts only canonical records.
Serving an older Core does not authorise that Core's legacy user-registration
writer. Reintroduced legacy state is diagnosed rather than silently adopted.

Persisted runtime references and live processes independently prevent pruning.
Migration retains retired references until the persisted user command completes
a normal MCP read with the expected Brain identity. Uninstall cleans owned
integration targets before deleting system files and retains shared user state,
CLI installation and managed runtimes. Shared Claude hooks survive ordinary
scope removal while another admitted route still needs them.

## Alternatives Considered

- Patch only upgrade's interpreter path: leaves install, repair, doctor,
  multi-Brain routing and cleanup with inconsistent ownership.
- A machine proxy daemon or another managed bootstrap environment: adds a
  service or another dependency-lifetime problem without a demonstrated need.
- A separate bulk-repair engine: duplicates the projection and safety rules.
- Legacy fallbacks throughout repair: makes canonical admission unverifiable
  and keeps two normal-path implementations indefinitely.
- Automatic adoption from live client files: confuses observation with user
  intent and risks overwriting custom configuration.

## Consequences

Core 0.70 / CLI 4 is one coordinated breaking setup release. Existing users must
migrate ownership before normal repair; unattended new setup must name clients.
Exact-file transactions detect drift and can roll back ordinary failures, but
do not promise crash-atomicity across repositories or native clients. Partial
results retain exact known effects and recovery evidence.

A configured transport is not proof of a connected host. Bootstrap health,
target/runtime readiness, an observed MCP round trip, and client reload/trust
remain separate outcomes. Native Windows execution is a release verification
gate, not something POSIX unit tests can establish.
