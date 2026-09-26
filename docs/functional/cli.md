# Brain CLI

The `brain` CLI is the machine-local projection of the Brain command architecture. CLI 3 keeps canonical command IDs in predictable noun/verb form while giving launcher-owned machine operations concise, domain-explicit entry points. It removes machine-implementation names and duplicate commands rather than carrying compatibility aliases.

## Command grammar

The launcher exposes `approvals inspect` and `approvals configure` for
[managed client policy](approvals.md), with explicit client/scope/surface selection.
These are host-local operations, not MCP tools. Doctor reports approval drift and
recovery; install offers a separate optional approval opt-in.

Successful no-effect launcher dry-runs do not require durable outcome receipt
storage. Reported actual, partial or uncertain effects still retain receipts,
including when a caller requested a preview; dry-run does not hide recovery evidence.

```text
brain [selection] <launcher-entry-point> [--request-json JSON|-] [--json] [--dry-run]
brain [selection] <noun> <verb> [--request-json JSON|-] [--json] [--dry-run]
brain [selection] session run [--operator-key KEY] -- <program> [args...]
brain [selection] <noun> <verb> --operation ID [--request-json JSON|-] [--json]
brain command list [discovery filters] [--json]
brain command describe <command-id> [--owner application|launcher|all] [--json]
brain --version
brain --help
```

Command IDs use `<noun>.<verb>`. Selected-Brain application commands use the same noun and verb as two CLI words. Launcher commands own an explicit entry point: the redundant `brain` noun is collapsed (`brain.doctor` → `brain doctor`), while other nouns remain visible (`runtime.inspect` → `brain runtime inspect`). For example:

```bash
brain artefact read --request-json '{"reference":"Designs/Example.md"}' --json
brain artefact create --request-json '{"type":"living/wiki","title":"Example","content":{"source":"inline","content":"Body"}}' --json
brain doctor --request-json '{}' --json
brain runtime inspect --vault /path/to/brain --request-json '{}' --json
```

There are no compatibility aliases, aggregate action buckets, parser spelling aliases or legacy-mode translations. Request fields are passed in one JSON object so MCP, CLI, direct script and typed Python use the same semantic request where those projections are eligible.

The CLI 3 launcher break is deliberate:

| Removed command | Replacement | Purpose |
|---|---|---|
| `brain machine migrate-legacy` | `brain migrate-legacy-installations` | Migrate legacy Brain installations on this machine. |
| `brain machine prune-runtimes` | `brain runtime remove-orphans` | Remove orphaned managed runtimes. |
| `brain backfill` | `brain register` | Register an installed Brain; the removed command duplicated this operation. |
| `brain prune` | `brain registry remove-stale` | Remove stale local Brain registry entries. |
| `brain runtime resolve` and `brain runtime resolve-runnable` | `brain runtime inspect` | Report the expected managed runtime and the selected runnable Python source together. |

`brain resolve` remains the direct registry lookup from Brain ID to vault path. These launcher commands are CLI-only; the MCP catalogue is unchanged.

`--request-json -` reads the object from standard input. Unknown fields, malformed JSON, unknown launcher entry points and application commands with anything other than one noun and one verb fail as request errors.

## Discover commands instead of memorising them

The installed catalogues are authoritative. Use discovery for the exact command set, request schema, version, owner, safety class, dependency tier, authority and current availability:

```bash
brain command list --owner all --json
brain command list --owner application --view detailed --page-size 8 --json
brain command list --owner application --query artefact --json
brain command describe artefact.create --owner application --json
brain command describe brain.upgrade --owner launcher --json
```

CLI 3.2 emits `brain.local-command-list/2`: flat entries and one `catalogues`
record per owner. The default is 25 brief entries per requested owner. An
explicit `--view detailed` retains complete summary metadata; application
schema detail still belongs to `command describe`. The application page's
16,000-byte limit is enforced by Brain Core 0.65; the composed CLI view also
includes launcher entries and is not itself an MCP result.

`--request-json` is accepted for discovery, using the same fields as the
flags, and cannot be mixed with discovery flags. It is no longer silently
ignored. Continue application pages with `--application-cursor '<JSON>'`
or request JSON `cursor`; continue launcher pages with
`--launcher-cursor '<JSON>'`. Retain the original filters. A null next cursor
means that owner has finished; request only the unfinished owner on later
pages. Detailed application views require Core 0.65 or newer; default brief
CLI rendering also works with earlier command-interface Brains.

`application` commands are owned by the selected Brain. `launcher` commands are machine-global and owned by the installed CLI distribution. The composed view preserves that owner and each catalogue's fingerprint; it does not create a third semantic catalogue.

## Select a Brain

Selection options are global and mutually constrained:

- `--vault PATH` selects an installed local Brain explicitly.
- `--brain ID` resolves one registered local Brain.
- `--workspace PATH` supplies the caller-local workspace used by normal workspace binding resolution.
- With no explicit selector, the CLI uses the canonical local resolution ladder.
- `--operator-key KEY` authenticates the application command against the selected Brain's profiles.

Launcher commands may run without a selected Brain when their schema permits it. Application commands always execute through the selected Brain's own `.brain-core/scripts/command.py`; the machine-global CLI does not import or emulate another Brain's application semantics.

### Workspace registration and policy

`brain workspace setup --workspace /absolute/repo --request-json '{}'`
converges canonical Brain registration and the caller-local binding. It requires
an already registered selected Brain and operator authority. Setup has composite
`selected_brain_and_caller_local` locality and
`selected_brain_and_caller_local_mutation` effects: it creates or attaches the
exact `living/workspace` hub, registers the local path in the selected Brain,
then writes `brain`, `slug` and the bare `links.workspace` key locally.

An existing `links.workspace` wins over the local slug and any registry path
match. With no link, this explicit setup operation uses the binding slug and
persists the link. Terminal workspaces require explicit reactivation. One
preparation, admission and receipt cover both boundaries; their mutation locks
are never held together. A local-write failure reports known Brain effects and
can be retried without creating another hub. The success payload separates
`registration` from `binding`.

`workspace.ensure-registration` accepts `key` and optional `title`, mutates only
the selected Brain and requires contributor authority. `workspace.update-policy`
accepts canonical `workspace`, optional `default_parent`, `clear_parent`, and
`default_tags` (an empty list clears tags). `workspace.update-metadata` retains
local tag/descriptive-link updates and adds `parent` / `clear_parent` for `defaults.parent`.
`links.workspace` is reserved to setup: metadata cannot set it and `clear_links`
preserves it. Generic artefact creation and document frontmatter edits reject
shared `default_parent` / `default_tags`; use the policy owner after registration.
Both parent policies require an existing non-terminal living artefact in the
same workspace; a workspace hub is self-scoped.

Setup and local metadata updates support CLI, direct-script and Python adapters,
and remain unavailable over MCP. Registration and shared policy support all
application projections. Session bootstrap reports `valid`, `unconfigured`,
`configured_invalid` or `terminal_inactive` and supplies repair guidance for
invalid or inactive bindings. A manifest's Brain alias must resolve to the
selected vault; an explicit selection of another Brain fails closed.

### Effective workspace context for content mutations

`artefact.create`, rename, naming-field/status/key changes, convert, reparent,
reparent-children, archive, unarchive, delete, set-workspace, `shaping.start` v2 and the four `document.*` mutation
commands accept the shared
semantic field `workspace_context`: omit it (or pass null) to use validated
startup context, use `workspace/{key}` to select a canonical workspace in the
selected Brain, or `global` for intentional unscoped operation. This is never
a path; CLI `--workspace /absolute/path` remains trusted adapter input. Invalid,
stale or inactive local bindings must be repaired even when supplying an override.

Create derives `workspace` membership and chooses its parent in this order:
explicit, local default, shared default, none. Local defaults apply only to the
locally bound workspace. Every ownership edge must be wholly global or within
one workspace. Shared, local and explicit tags are additive and deduplicated;
the normal parent relationship tag remains derived.

Document mutations apply this policy only to artefact targets. A selector on a
memory, skill, style or template is rejected. Edits preserve membership and
parent, including when explicitly selecting another workspace; that selected
workspace contributes its configured tags. Removing configured tags is undone
by semantic editing; change policy or explicitly select `global` to stop adding
them. Generic create/frontmatter inputs cannot write `workspace`.

Preparation reviews and affected typed results include `mutation_context`, its
separate shared/local inputs, effective parent/tags and source revisions. Policy
source drift invalidates a prepared operation before writes. If index refresh
fails after a workspace-aware mutation commits, its known-partial
result retains that exact `mutation_context` alongside committed subjects and
the index-repair action. Generic persisted receipts remain privacy-minimal.
Maintenance-only link and repair rewrites do not acquire policy tags.

`shaping.start` resolves one policy snapshot for its composite operation. A new
temporal transcript receives membership, local/shared default parent (or none),
configured tags and its explicit template tags. Source links are references, not
ownership; transcripts remain temporal leaves. The source and any continued
transcript preserve their membership and parent while restoring configured tags.
Continuation follows an existing same-day source-linked transcript even when
parent defaults change. Preparation binds both subjects and policy inputs; one
lock, admission and receipt cover lifecycle, transcript and backlink writes.
Known partials retain exact context, actual committed paths and index repair
guidance. The lower `start_shaping_session.py` is an internal mechanical primitive,
not a public workspace-aware adapter.

Lifecycle transitions preserve membership and apply policy tags to every
surviving semantic subject. Recursive archive/unarchive/convert include the
selected descendants; reparent-children includes the direct children, not
incidental descendant path moves or backlink rewrites. Delete applies no tags.
Reparent and conversion outcomes must retain same-workspace ownership edges.

Configured default parents cannot be deleted, archived, made terminal, converted
out of living classification or given a different canonical key. Workspace hubs
cannot be archived, deleted or re-keyed while discoverable member/policy references
or the active local binding remain. Guards inspect active and terminal living,
temporal and archived artefacts and the invocation's local manifest; they cannot
inspect disconnected clones. Clear or replace reported policy references before
retrying. Terminal workspace hubs remain historical identities but cannot be
selected as effective mutation context. Hub type conversion fails closed because
ordinary conversion cannot clear implicit self-membership.

`artefact.set-workspace` explicitly assigns its effective `workspace_context` as
membership. `global` explicitly clears membership; an unconfigured implicit
context cannot clear it. Requests accept `path`, `recursive`, optional `parent`,
and `clear_parent` (mutually exclusive with `parent`). Omitted parent is preserved
only when compatible with the destination. Creation defaults do not replace the
root parent during reassignment.

Owners with descendants require `recursive: true`. The transition discovers
active and terminal living records, parented temporal records, and archived
records from explicit frontmatter, not the compiled living index alone. Only
living canonical identities own descendants; temporal keys are vestigial and
attempted temporal-owner edges fail closed. Duplicate living identities affecting
the transition are ambiguous, including active/archive duplicates.

Every selected subject receives destination membership and configured tags.
Internal parent edges remain intact; replacing/clearing the root parent updates
active/terminal folder projections and backlinks. Archive locations remain
manual and unchanged. Explicit tags are retained except the intentionally
changed root-parent relationship tag. The complete ownership snapshot and
write/move set are admitted under one mutation lock; partial results identify
actual committed subjects, exact context, and any required index repair.

`vault.check` version 3 exposes optional finding `code` values under the
`workspace_contract` check, including reference, ownership, policy, local binding,
and adoption-candidate diagnostics. It inspects the caller-local manifest only
when that trusted adapter context is available; disconnected clones are not scanned.

### Skill sources and exposure

Selected-Brain commands `skill.list`, `skill.status`, `skill.add-git`,
`skill.update` and `skill.detach` own package source state. Launcher commands
`skill.expose` and `skill.unexpose` own explicit writes to Claude, Codex and Grok
discovery directories. For example:

```bash
brain skill update --request-json '{"name":"shaping"}' --json
brain skill expose --request-json \
  '{"name":"shaping","client":"all","scope":"global"}' --json
```

The exposure launcher still requires an active Brain so it can validate the
effective skill. With no explicit selector, normal resolution includes the
machine's default Brain. Project exposure resolves an existing canonical binding
for `--workspace` first and otherwise uses the machine default; it never creates
or changes a binding.

### Permission administration

`permission.set-profile` assigns an existing registered operator to an existing profile in the selected Brain. Preview reports the exact permission difference and current configuration revision:

```bash
brain --vault /path/to/brain --dry-run permission set-profile \
  --request-json '{"operator_id":"agent","profile":"contributor"}' --json
brain --vault /path/to/brain permission set-profile \
  --request-json '{"operator_id":"agent","profile":"contributor","expected_revision":"sha256:…"}' --json
```

An explicit registered administrator-profile key is required. If `--operator-key` is omitted, an interactive terminal prompts; non-interactive use must supply it. The selected-Brain service re-authenticates and compares the preview revision under its mutation lock before recording audit intent and applying the profile change. The audit records actor, target, before/after profiles, exact permission changes and configuration revisions, without credential material. An uncertain final outcome must be inspected through the selected Brain's `invocation.read`, using the same principal; it is never automatically replayed.

This command does not edit the default principal and is absent from MCP and selected-Brain `command.py`. Agent consent uses the dedicated `access.request` tool within existing credential permissions. Configure the harness's approval policy for that tool; Brain no longer runs an external approver or timed lease workflow.

### CLI jobs and consent

Use an explicit job when several CLI calls need the same exceptional consent:

```bash
brain --vault /path/to/brain session run -- python3 workflow.py
```

The supervisor authenticates before launching the program and pins that Brain and principal. Starting a job grants no exceptional access. Its descendants can prepare an operation with `access.prepare`, explicitly request its reviewed scope with `access.request`, check the decision, then invoke the ordinary command with `--operation ID`. Without that selector, only initial authorisation or an exact-command blanket grant applies. Use `command.describe` for the current preparation and request contracts. The CLI does not request consent automatically, and harness MCP-tool approval rules do not intercept shell commands inside the job.

Consent ends when the root program exits, even if background children remain. A new job starts without exceptional consent. Standalone calls retain configured initial access, but cannot request reusable exceptional grants; their errors direct the caller to start a job. Missing or broken job channels cannot be replaced with a public context ID, copied environment value or repeated credential.

Job transport requires POSIX inherited descriptors. It is verified on macOS; Linux needs its own platform verification. Native Windows private-owner transport is unsupported: CLI jobs are unavailable, and MCP can perform configured initial operations but cannot prepare or grant exceptional consent or retain context reductions. Existing owner loss always fails rather than becoming a standalone call.

Shells normally preserve the inherited channel. Python's `subprocess` closes extra descriptors by default, so a job's Python program must deliberately forward it to trusted Brain subprocesses. The installed Core supplies the forwarding helper:

```python
import os
from pathlib import Path
import subprocess
import sys

# This program is launched by `brain session run`.
core_scripts = Path(os.environ["BRAIN_VAULT_ROOT"]) / ".brain-core" / "scripts"
sys.path.insert(0, str(core_scripts))
from _bootstrap.owner_attachment import OwnerAttachment

attachment = OwnerAttachment.capture()
if attachment is None:
    raise RuntimeError("Start this workflow with brain session run")
try:
    subprocess.run(
        ["brain", "access", "status", "--json"],
        check=True,
        **attachment.forwarded_process(),
    )
finally:
    attachment.close()
```

Capture once before launching subprocesses and retain the attachment for the workflow. `forwarded_process()` supplies the matching environment and `pass_fds`; forward it only to children that should share the job's consent. Ordinary provider subprocesses receive neither the locator nor the descriptor. Closing this local attachment releases its channel; the supervisor still owns the job lifetime.

## Dependency planes

The command catalogue declares `bootstrap`, `portable` or `managed` as an ordered minimum dependency tier. The CLI runs bootstrap and portable application commands with its Python 3.12+ launcher, and resolves the selected Brain's managed runtime only for managed commands. Locality and provider requirements remain separate catalogue facts; a higher dependency tier does not imply machine-global ownership or remote transport.

## Results and exit categories

`--json` emits exactly one `brain.command-result/1` envelope. Human mode renders the same envelope without changing its semantics.

- `ok` contains a typed result.
- `partial` lists each known committed effect. When machine-local cleanup is
  still required, typed `error.details.recovery_paths` lists the exact absolute
  paths that remain.
- `error` has `effects: none` or `effects: unknown`.
- Unknown mutation outcomes are non-retryable and include an outcome reference for `invocation.read`.

Exit categories are stable across CLI and direct script:

| Exit | Category |
|---:|---|
| 0 | Success |
| 1 | Known partial outcome |
| 2 | Usage, request or domain error |
| 3 | Authority or capability unavailable |
| 4 | Infrastructure failure or unknown mutation outcome |

## Launcher recovery and old Brains

CLI 3 can identify and recover an installed Brain older than 0.55.0, but it does not translate old grammars. Launcher-owned version, doctor, install and upgrade/recovery commands remain available. Attempting an application command returns structural `upgrade_required`; that Brain's own legacy scripts remain directly invocable until the Brain is upgraded.

`brain.upgrade` v2 performs a complete-registry preflight and coordinates Brain Core 0.55+, the installed CLI, catalogue, manifest and proxy contracts. Known other pre-cutover Brains require `acknowledge_global_cli_cutover: true`. Stale registry IDs require an exact sorted `excluded_stale_brain_ids` list; unknown registry scope cannot be waived.

After provisioning the target managed runtime, upgrade invokes the compatible machine CLI's ownership migration and Brain-breadth MCP repair. This covers shared user registration even when the vault has no project registration, and the selected Brain's registered external targets. Registration or readiness failure is a known partial outcome with explicit effects and recovery guidance, not a false success. Upgrade then starts or joins the selected Brain's canonical runtime warm-up and waits for a recorded `ready` state. Its read-only machine inspection recommends explicit runtime removal only when both persisted-registration coverage and live-process inspection permit it.

## MCP registration and repair

`mcp.configure` v3 requires `client`: `claude`, `codex`, `grok`, or `all`.
Claude supports `project`, `local`, and `user`; Codex and Grok support project
and user only. `all` with local scope selects Claude and reports the exclusions.
Configuration never creates a workspace binding or changes the machine default.

```bash
brain mcp configure --request-json '{"client":"all","scope":"user"}' --json
brain mcp configure --workspace /path/to/project --request-json '{"client":"claude","scope":"project"}' --json
brain mcp configure --request-json '{"client":"all","scope":"user","action":"remove"}' --json
brain mcp repair --request-json '{"scope":"user"}' --json
brain mcp repair --workspace /path/to/project --json
brain mcp repair --vault /path/to/brain --request-json '{"breadth":"brain"}' --json
brain mcp repair --request-json '{"breadth":"machine"}' --json
```

User configuration/removal/repair needs no selected or healthy Brain. It manages
the connection, not target readiness. Workspace repair (the default breadth)
repairs recorded projections against an existing runtime. Brain breadth also
reconciles that Brain's runtime, vault-self and all registered targets; machine
breadth composes all registered local Brains. Both include recorded shared user
projections once. Broad requests omit client/scope filters. `--dry-run` reports
the same admitted workset without writes. Results separate native scope, repair
breadth, target paths, runtime steps and known file effects.

Repair restores missing owned files but never installs an unselected client.
Unowned or modified Brain slots, damaged ledgers, unavailable registered targets
and missing reverse coverage stop admission. Unrelated native client settings
are preserved. Shared Claude hooks/bootstrap survive ordinary scope removal
while an admitted sibling or user route still needs them.

Brain registry unregister and stale-entry removal refuse surviving canonical
integrations or unowned native Brain slots. Remove those integrations explicitly,
or use composed Brain uninstall, before dropping their inventory root. CLI
replacement takes the same machine registration lock as projection mutation, so
capability checks and cutover cannot race a new shared registration.

### Migration and bootstrap recovery

Install CLI 4 before changing user ownership. Then inspect and migrate:

```bash
brain mcp migrate --dry-run --json
brain mcp migrate --json
brain mcp repair --request-json '{"breadth":"machine"}' --json
brain doctor --json
```

Migration admits exact recorded legacy claims, recovers known reverse targets,
and moves shared user claims into the machine ledger. It preserves custom or
ambiguous state for explicit resolution. Rerun migration to resume a journalled
interruption; do not delete its before/after evidence. Retired runtime references
remain protected until the persisted user command completes a normal MCP read
with the expected Brain identity. An unavailable Brain can therefore leave a
committed migration with verification still pending; the receipt reports partial
effects, not successful connectivity. Older Core direct user writers are not
supported after cutover, even where serving that Core remains compatible.

Brain preserves native client approval policy during migration,
configuration and repair. Codex's `default_tools_approval_mode`, tool allow/deny
lists and per-tool `approval_mode` are not transport ownership conflicts.
Claude permission/approval settings and Grok's sibling permission/UI tables
remain client-owned and unchanged. Changed commands, environment, routing and
unrecognised server options still require explicit resolution. Explicit server
removal removes its nested Codex policy along with that server; it does not
change sibling/global client permission settings.

User entries launch an absolute installed `brain mcp serve` command. The checked
distribution records its base Python separately from Brain managed runtimes.
Stdio startup neither searches PATH nor installs anything. If the recorded base
Python moves, reinstall from a complete source checkout with an explicit base
interpreter (outside a virtual environment):

```bash
/absolute/python3.12 cli/_distribution.py /path/to/source /path/to/prefix/bin/brain --bootstrap-python /absolute/python3.12
```

Use `brain.cmd` at the Windows destination. Doctor reports bootstrap availability
separately from registration state. Successful repair does not reload an already
running MCP host; reconnect/restart that host as required by the runtime-drift
diagnostic. CLI replacement refuses to remove stdio capability while persisted
user registrations still depend on it.

## Installation

The installer writes a versioned distribution under the selected prefix and a small platform bootloader under `bin/`:

- Unix-like user install: `~/.local/bin/brain` and `~/.local/lib/brain-cli/4.0.5/`.
- Native Windows user install: `%LOCALAPPDATA%\Programs\Brain\bin\brain.cmd` and the adjacent `lib\brain-cli\4.0.5\` distribution.

The distribution contains the launcher application plus the Brain Core payload needed for install, upgrade and selected-Brain execution. Installation and replacement verify a content manifest and executable identity; failed replacement restores the proven old binary/distribution pair or retains recovery material and reports the outcome as unverified. Failed upgrade results carry every known absolute recovery path in the structural error and durable launcher receipt: residual staging material after a verified rollback is a known partial outcome, while unverified rollback remains outcome-unknown. Standalone human output lists the same paths before the failure message. Once the new pair is verified, failure or interruption while removing an old backup is committed post-upgrade recovery work and never rolls Brain Core back to an older version. Both the launcher result and standalone distribution JSON list the surviving `cleanup_recovery_paths`.

The bootloader requires Python 3.12 or newer. `BRAIN_CLI_VERSION` is `4.0.5`; `BRAIN_INSTALL_REF` is `v0.70.9`.

JSON command invocations validate the structural stdout envelope, including
command identity, version and exit category. Incidental child stderr does not
replace a valid result. Portable diagnostics may delegate an active semantic
check to the selected managed Python runtime.

## Native Grok setup

`brain install`, `brain mcp configure`, `brain agent-skill configure`,
`brain skill expose` and `brain skill unexpose` accept `"client":"grok"`;
`"all"` includes all three supported clients. `brain mcp repair` recognises
existing Grok project state, and recorded uninstall removes owned Grok files.
For the native paths, startup rule and preservation contract, see
[Grok client configuration](config.md#grok-client-configuration).

```bash
brain mcp configure --request-json '{"client":"grok","scope":"project"}' --dry-run --json
brain mcp configure --request-json '{"client":"grok","scope":"project"}' --json
brain agent-skill configure --request-json '{"client":"grok"}' --json
brain skill expose --request-json '{"name":"shaping","client":"grok","scope":"project"}' --json
brain mcp configure --request-json '{"client":"grok","scope":"project","action":"remove"}' --json
```
