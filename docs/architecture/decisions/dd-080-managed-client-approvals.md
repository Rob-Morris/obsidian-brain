# DD-080: Registration-driven managed client approvals

**Status:** Accepted
**Extends:** DD-073, DD-078

## Context

Host approvals reduce repetitive prompts but are not Brain permissions or
exceptional consent (DD-073). Transport registration (DD-078) already owns machine
inventory, locks and projection boundaries. Treating client policy as transport
would let one vault overwrite shared policy or discard user settings on removal.
A hand-maintained tool list would drift after releases.

## Decision

Offer one explicit normal read/write profile, with separately selected clients,
native scopes and MCP/CLI surfaces. Export bounded facts from the canonical
application, launcher and proxy catalogues. Keep policy pure and native syntax at
the boundary. Store exact approval ownership separately from transport and synced
vault configuration. Reconcile desired, last-applied and observed values; equal
existing rules remain unowned without explicit adoption.

Reuse registration locking and `FilePlan` around target transitions. Tighten before
exposing changed routes/contracts; expand after commit. A durable active marker and
per-operation OS lock cover the unlocked interval needed by child CLI operations.
An interrupted operation retains bounded write-intent evidence and recovers against
actual committed inventory, without claiming multi-file crash atomicity.

Preserve user asks/denies, edits/deletions, availability controls and unrelated
settings. Explicit literal legacy migration records source restoration evidence.
Native effective policy and file installation are separate outcomes: report
overrides and host verification requirements. Brain does not grant workspace trust,
request exceptional consent or reconnect clients to activate their policy.

## Alternatives Considered

- Copy today's manual rules: duplicates command knowledge and drifts on rename.
- Own the whole transport subtree: loses client policy and mis-scopes shared state.
- Blanket server/shell allows: authorise unknown operations beyond the opted-in policy.
- Require opt-in for each new normal command: contradicts the selected trust in future
  classifications within the profile.
- A background daemon or automatic client restart: adds lifecycle ownership without
  being needed for supported install, upgrade, configuration and repair operations.

## Consequences

New normal commands follow the profile automatically; policy-boundary changes need
a new opt-in. Mixed classifications retain review. Missing contracts and ambiguous
registries produce actionable incomplete outcomes. CLI rules consider all known
Brains even in project scope because trailing arguments can select another vault.
Native rules cannot confine unregistered explicit paths or out-of-band executable
replacement. Client trust, reload, version support and Windows shell certification
remain explicit capabilities verified independently of JSON/TOML validity.
