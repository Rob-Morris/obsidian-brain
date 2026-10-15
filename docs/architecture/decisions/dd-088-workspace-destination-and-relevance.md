# DD-088: Workspace destination, defaults and relevance

**Status:** Accepted
**Extends:** DD-051

## Context

[DD-051](dd-051-workspace-owned-binding-and-workspace-aware-mcp.md) separates
workspace identity from transport. Its canonical-identity and folder-registration
amendment makes Brain plus slug the durable identity and permits several local
folders to register against it. Those contracts remain in force.

Content creation still needs to distinguish where work belongs from where the
agent started. A task raised while working in one workspace can belong to
another. Conflating the startup workspace, structural parent and relationship
tags either misfiles that task or makes associations look like ownership.
Historical relevance also needs to survive a workspace ceasing to be active.

## Decision

### Independent identity, membership and parentage

A canonical `living/workspace` record is a self-scoped root. It cannot have a
structural parent, including a global project. Its optional `default_parent`
selects a non-terminal living content parent inside that workspace; it does not
place the workspace record under that parent. Workspace creation does not
inherit the caller's policy, parent or source-relevance tag.

Other artefacts have at most one primary `workspace: workspace/{key}` and one
structural `parent`. Membership alone does not imply parentage. Parentless
content retains membership. Every parent edge stays within one workspace or
wholly global. Existing ownership moves only through explicit
`artefact.set-workspace`, with its complete-graph validation and recursive
requirements; tags and ordinary edits never adopt content.

### Select destination before loading defaults

For `artefact.create` version 5, destination precedence is:

1. Explicit symbolic `workspace_context` (`workspace/{key}` or `global`).
2. A non-null explicit parent's primary membership, including global membership.
3. Validated startup workspace, or global when unconfigured.

Contradictory explicit destination and parent refuse without writes. Overrides
never bypass a broken startup binding. Destination shared policy applies; local
policy applies only when destination equals the bound workspace. Parent
precedence is explicit value, local default, shared default, none. Explicit
`parent: null` suppresses defaults; omission uses them. Top-level `parent` and
`frontmatter.parent`, when both supplied, must agree.

Cross-destination creation adds the bound source workspace's canonical tag as
relevance, including when the destination is global. That association is editable
and does not change destination membership or structural parent. Preparation and
results expose the source, selected workspace, selection reason, parent source,
relevant workspaces and observed policy revisions. Ordinary mutations preserve
membership while applying their selected policy; a selector is not reassignment.

### Discover relevance separately from primary membership

`artefact.list` and `artefact.search` accept optional canonical `workspace` and
`workspace_match: relevant | primary`, defaulting to `relevant`. Relevant means
primary membership plus exact canonical workspace tags. Primary means membership
only. Omission is vault-wide, never an implicit startup-workspace filter.
Canonical filter syntax is validated independently of target existence or
lifecycle. Discovery grants no mutation authority and selects no policy.

Results project nullable `primary_workspace` and `relevant_workspaces`, with
primary included in relevance. Workspace records project their own identity.
Search's lexical, semantic and hybrid modes use the active namespace, including
terminal status folders, and exclude archives. Exhaustive list supports active,
archived and combined enumeration with the same metadata projection.

### Preserve soft history without accepting new accidental identities

New authored workspace associations in creation, explicit tag edits and policy
updates must resolve to one existing active or archived workspace. An association
to a historical identity does not select that identity's defaults. Existing stale
tags remain editable, and inherited policy tags are not treated as newly authored
on each application. Missing and malformed historical relevance produce
informational findings, not ownership errors or adoption candidates.

Relevance tags alone do not block workspace archive/deletion and remain authored
history afterwards. Hard membership/default-parent references, recorded folder
registrations, embedded locations and the invocation's binding still protect
identity. Guards cannot inspect unrecorded disconnected clones.

A workspace rekey that passes those guards rewrites exact canonical relevance
tags in active and archived living and temporal artefacts. Similarly prefixed
tags remain unchanged. Archived reference reads and affected-file revisions are
part of preparation, so concurrent historical edits invalidate the prepared
operation. Archive/deletion preserves relevance rather than erasing it.

### Upgrade policy A: format conversion and explicit repair

Automatic upgrade work is limited to lossless format conversion. Ownership
adoption/reassignment and identity renames require an explicitly approved repair
through existing public commands. Neither relevance tags nor a project having
only one associated workspace supplies ownership intent. Approval to rename one
workspace does not establish a generic rename rule.

The compatibility transition is introduced in Core 0.73.0. The preflight scans
Brains from earlier Core versions; upgrades from 0.73.0 or later do not repeat
that ownership migration scan. Ordinary workspace checks remain available for
subsequent content drift, and folder-format migration retains its own versioning.

Before Core cutover, a read-only source-target compatibility preflight uses the
target Core's canonical validators in a fresh import context. It checks actual
hub ownership and shared default-parent compatibility as well as workspace identity and
status. A refusal identifies affected records and remediation before Core copy,
VERSION changes or creation of a new rollback journal. Recovery of an existing
interrupted-run journal remains a prerequisite; previews may defer the verdict
until the recovered state can be checked.

The operator reviews and approves the intended repair, applies it using existing
public commands, retains recovery checkpoints and reviews intermediate effects.
Multi-command repair is not atomic; verify the complete chosen scope before
retrying upgrade. Released Core 0.72 provides the required
repair commands. When the installed Core lacks them, including historical
releases before 0.69, the supported bridge is an explicit source upgrade from a
coherent released 0.72 source: `--dry-run` first, then normal upgrade. Perform the
approved repair in a vault-only context without an external `--workspace` binding,
verify it, then retry the target upgrade. No automatic bridge or downgrade is
permitted. Do not bypass validation with internal-state edits.

Core format migrations execute under the runner's already-held non-re-entrant
mutation lock and use internal helpers compatible with that lock. They do not
re-enter public commands to perform repairs. Registered-folder format conversion
stays in its existing separately admitted post-commit phase, with separate
receipts, offline coverage and resumable partial outcomes; it is not preflight
admission or part of the vault-only rollback journal. A preflight refusal does
not cause an automatic MCP reset.

The existing registered-folder format phase validates the active canonical hub
and shared policy, but preserves local defaults without validating them. Format
success therefore does not prove that workspace-scoped mutations are ready.
Check local policy after conversion to the current format; repair invalid local
defaults explicitly with `workspace.update-metadata`. Do not add local-default
validation before format conversion: that would block the conversion needed to
make the repair available.

This is the confirmed policy for the parent implementation on `dev`. The release
proposal is deferred and VERSION is unchanged; this decision assigns no new
migration version, public command or automatic repair flag.

## Alternatives Considered

- Always use startup destination: loses the explicit parent's ownership intent
  and makes cross-workspace creation load the wrong defaults.
- Use the workspace record as an implicit parent: conflates membership with
  structural filing and makes optional project policy compulsory.
- Infer adoption from workspace tags: prevents several associations and turns
  historical metadata into an ownership obligation.
- Require live targets for discovery or every edit: hides history and blocks
  unrelated edits after archive/deletion.
- Rewrite only active relevance on rekey: strands archived associations under
  the old identity. Prefix replacement would also corrupt unrelated tags.

## Consequences

Agents can create content for another destination without switching connection
state, while preserving why it matters to the source workspace. Enumeration
must choose primary membership explicitly when that is the intended question.
Tags remain useful history without weakening hard ownership guards.

The distinction between omitted and null parent is part of the creation contract,
not a transport convenience. Discovery and mutations intentionally have different
target-lifecycle validation. New source and policy observations must remain bound
to preparation and known-partial results.

## Implementation Notes

`workspace_context.py` owns destination/default resolution; `_common/_workspace.py`
owns membership, ownership validation and discovery projection.
`workspace_associations.py` validates new authored associations. Listing and
lexical/semantic retrieval consume the shared workspace-match contract.
`workspace_integrity.py` owns hard lifecycle guards; canonical-reference planning
in `edit.py` opts workspace rekeys into archive scanning.

Current contracts: [configuration](../../functional/config.md#workspace-manifest),
[CLI](../../functional/cli.md#workspace-discovery-filters),
[MCP](../../functional/mcp-tools.md#workspace-discovery-filters) and
[user workflows](../../user/workflows.md#workspace-aware-changes).
