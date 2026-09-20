# Brain Workflows

Day-to-day usage patterns for working with the Brain.

For optional standing approvals of normal Brain reads/writes, use
[managed client approvals](../functional/approvals.md). Select MCP, CLI or both
explicitly; inspect drift before repair and verify activation in the client.

---

## Workspace-aware changes

When a local workspace is bound, creation and semantic edits use its shared and
local default tags. Lifecycle operations (rename, status/key/type changes,
reparent, archive and restore) also apply these tags to the selected surviving
artefacts while preserving membership. Recursive operations include their
explicitly selected descendants. Backlink fixes and maintenance do not add tags.

Use the symbolic `workspace_context` field to select another `workspace/{key}`
or `global` for one operation. Ordinary mutations do not reassign existing artefacts;
the dedicated `artefact.set-workspace` command does.
Invalid local bindings must be repaired before either choice can proceed.

Before retiring a default parent, replace or clear the shared/local parent
policy. Before deleting, archiving or re-keying a workspace hub, resolve the
member and binding references reported by the guard. Closing a workspace with a
terminal status preserves historical membership but stops its use as mutation
context. Hub type conversion is refused because it would change self-membership.
Only the selected vault and active local manifest are inspected; disconnected
clones validate their bindings when next used.

If a mutation reports partial completion, inspect its committed subjects,
effective context and repair action before retrying. Do not assume that a failed
index refresh rolled back the content change.

### Explicit adoption and reassignment

Checkpoint the vault before a bulk adoption and upgrade to a runtime that describes
`artefact.set-workspace`. Review `vault.check` findings and the intended ownership
tree first. Existing `workspace/*` tags are relationship evidence, not permission
to adopt records automatically.

For an existing unscoped project and its owned subtree, these commands provide a
deliberate rollout (replace the example identities and selected vault):

```bash
brain workspace ensure-registration --vault /path/to/brain --request-json '{"key":"example"}'
brain artefact set-workspace --vault /path/to/brain --request-json '{"path":"project/example","workspace_context":"workspace/example","recursive":true,"clear_parent":true}'
brain workspace update-policy --vault /path/to/brain --request-json '{"workspace":"workspace/example","default_parent":"project/example"}'
brain vault check --vault /path/to/brain --request-json '{"check":"workspace_contract","actionable":true}'
```

Use `clear_parent` only when intentionally making the root top-level; otherwise
provide a same-workspace replacement `parent`, or omit both to preserve a compatible
parent. Shared/local default parents do not implicitly reparent adoption roots.
The recursive command includes terminal living, parented temporal, and archived
records. Temporal artefacts are leaves, even if they carry a vestigial key.
Archive locations stay unchanged; active owner-derived paths and backlinks follow
an explicit root-parent change. Ambiguous identities and cyclic ownership fail
closed before writing.

Preserve existing local defaults unless deliberately changing them with
`workspace.update-metadata`. Verify session binding, a newly created child's
membership/parent/tags, and index health after adoption. Inspect any known partial
result before retrying; checkpoint rollback is an explicit whole-vault action,
never an automatic response to a reported partial write.

## A Day in the Life

Here's what working with the Brain looks like in practice.

### Morning: You Start Working

You're building a new feature for a side project. Before diving into anything complex, you write a quick plan:

```
_Temporal/Plans/20260321-auth-redesign.md
```

```yaml
---
type: temporal/plan
tags:
  - plan
  - project/my-app
status: draft
---
```

The plan captures your intended approach: what you're going to do, which files you'll touch, what the goal is. It takes two minutes and saves you from going in circles.

### During the Day: Capturing What Happens

As you work, you (or your agent) append entries to today's log:

```
_Temporal/Logs/20260321-log.md
```

```
09:30 Started auth redesign. Replacing session tokens with JWTs.
11:15 Hit a snag with refresh token rotation. See [[auth-redesign]].
14:00 Resolved — using sliding window expiry. Decision captured in [[20260321-decision~JWT Refresh Strategy]].
```

Entries are brief, timestamped, and link to relevant artefacts. The log is append-only — you never go back and edit it. It's the raw timeline.

### A Decision Worth Recording

That JWT refresh strategy was a real fork in the road. You had three options, debated the tradeoffs, and chose one. Before the reasoning fades, you capture it:

```
_Temporal/Decision Logs/20260321-decision~JWT Refresh Strategy.md
```

The decision log records what question you faced, what options you considered, and why you chose what you chose. Six months from now when someone asks "why sliding window?", the answer is right there.

### An Idea Strikes

While debugging, you notice the token validation could be generalised into a shared library. It's not what you're working on, but you don't want to lose it:

```
_Temporal/Idea Logs/20260321-idea-log~Shared Token Validation.md
```

Captured in 30 seconds. The bar is deliberately low. Most idea logs won't go anywhere, and that's fine. The ones that matter will graduate later.

### Something Generates Friction

The API docs say one thing but the code does another. You waste 20 minutes figuring out the actual behaviour. Before moving on, you log the friction:

```
_Temporal/Friction Logs/20260321-friction~API Docs Mismatch.md
```

One friction log is just a note. But when the same kind of friction keeps showing up, you distil it into a gotcha (`_Config/User/gotchas.md`) so your agents know to watch for it.

### After Work: Journaling

Work's done for the day, but something's on your mind. You've been thinking about a conversation with a friend, or processing a big life change, or just want to get some thoughts down. You chat with your agent about it — casually, like talking to a friend.

The agent captures what you shared as a journal entry, in your own words:

```
_Temporal/Journal Entries/20260321-journal--personal--moving-house.md
```

```yaml
---
type: temporal/journal-entry
tags:
  - journal-entry
  - journal/personal
---
```

The entry records your reflections. The conversation itself is a separate transcript if worth keeping. Journal entries are distinct from logs (which track work) and thoughts (which are fleeting fragments) — they're developed personal reflections in your own voice.

If you have multiple journals — say, a personal one and a health one — each is a living artefact in `Journals/` that groups its entries via a nested tag like `journal/personal` or `journal/health`.

### End of Day: The Daily Note

At the end of the day, you (or your agent) create a daily note that distils the log:

```
Daily Notes/2026-03-21 Fri.md
```

```markdown
## Tasks
- [x] Auth redesign — JWT migration
- [x] Decided on sliding window refresh strategy
- [ ] Update API docs (carried forward)

## Notes
### Auth Redesign
Replaced session tokens with JWTs. Main decision was refresh strategy —
went with sliding window expiry over fixed-lifetime tokens. See
[[20260321-decision~JWT Refresh Strategy]].
```

The log is the raw timeline. The daily note is the digest.

---

## Working with Tasks and Notes

**Tasks** (`Tasks/`) are persistent units of work — things you're tracking across sessions, not one-off to-dos. They have status values (`open`, `in-progress`, `done`, `parked`, `deprecated`) and link to related artefacts.

**Notes** (`Notes/`) are low-friction personal working documents for knowledge you want to retain and develop. They can stay exploratory, be reorganised as understanding grows, or be converted into Wiki, Documentation, Design, or another more formal artefact when their role changes.

The daily note's task list is a digest of what happened, not the authoritative record. Authoritative task status lives on the task artefact itself.

---

## When Ideas Grow Up

Some temporal captures deserve to become living artefacts. Here's one common progression — not a required pipeline, but a pattern that happens naturally.

### Stage 1: Raw Capture

You had that idea about shared token validation. It's sitting in an idea log — a temporal snapshot.

### Stage 2: Living Idea

A week later, you keep thinking about it. Time to flesh it out:

```
Ideas/Shared Token Validation.md
```

```yaml
---
type: living/idea
tags:
  - idea
  - project/my-app
status: new
---
```

```markdown
**Origin:** [[20260321-idea-log~Shared Token Validation|Original idea log]] (2026-03-21)
```

The idea doc explores the concept: what would this library look like? What would it need to handle? It's still loose — no prescribed format beyond the frontmatter.

Back on the idea log, a callout records the spin-out:

```markdown
> [!info] Spun out to idea
> [[shared-token-validation]] — 2026-03-28
```

### Stage 3: Design

The idea has legs. Time to shape it properly:

The shaping skill reads the design taxonomy, chooses its conversational mode, and calls the granular `shaping_start` MCP tool to open or continue today's linked session. The application command owns transcript and taxonomy-declared status mechanics; the skill owns adaptive questions, answer propagation, reconciliation, and the completion decision. Most types enter `shaping` and later move to their declared completion status. Discovery-shaped types whose lifecycle represents an enduring state may instead preserve their current non-terminal status throughout the pass.

Shaping uses the same workspace context as other semantic mutations. New
transcripts receive the selected workspace's membership, default parent and tags;
source links are references rather than ownership. The source and any continued
transcript retain their membership and parent while restoring configured tags.
Use symbolic `workspace_context: global` for an unscoped new transcript, or
`workspace/{key}` for another workspace's policy; filesystem paths remain adapter
inputs. Invalid local bindings must be repaired before shaping, even with an
explicit selector. A partial result identifies committed files and any required
index repair; inspect it before retrying.

The source artefact remains the current truth, while transcript reconciliation events preserve how decisions, work, possible questions, and body content were added, narrowed, resolved, reopened, or propagated. Each turn asks for one user commitment; question numbers identify transcript turns while stable decision numbers identify the artefact's evolving choices. At a candidate stopping point, the skill explains at a high level why the taxonomy bar is met and recommends an optional four-Cs review—independent when a separate reviewer is available. The review checks correctness, clarity, consistency, and completeness, asks before applying fixes, and either supports completion or returns decision-worthy gaps to shaping. The user chooses to run the review, skip it and complete or hand off the pass, or stop without asserting completion; status changes only when the taxonomy and chosen outcome require one.

```
Designs/Shared Token Validation.md
```

```yaml
---
type: living/design
tags:
  - design
  - project/my-app
status: shaping
---
```

```markdown
**Origin:** [[shared-token-validation|The idea]] (2026-03-28)
```

The design doc has structure: a core goal, open decisions, transcripts from Q&A sessions that shaped it. It moves through `shaping` → `ready` → `active` → `implemented`.

Set the idea's status to `adopted` with the `artefact_set-status` MCP tool; Brain moves it to `Ideas/+Adopted/` and updates wikilinks vault-wide. If the idea is later revived with a non-terminal status, the same handler moves it back out.

### The Thread is Never Lost

At every stage, origin links connect child to parent. You can trace the thread from a shipped feature all the way back to the moment the idea first crossed your mind. The Brain remembers the journey, not just the destination.

---

## Building Knowledge Over Time

Not everything follows the idea-to-design path. Some artefacts are about accumulating understanding.

### Wiki Pages

Your wiki is a curated knowledge base. One page per concept, polished and comprehensive. You write a wiki page about JWT refresh strategies after going through the auth redesign — distilling what you learned into reusable reference:

```
Wiki/JWT Refresh Strategies.md
```

Wiki pages are evergreen. You come back and update them as your understanding deepens. They're deliberately selective — not everything needs a wiki page, just the things worth explaining properly.

### Research Notes

Before writing that wiki page, you probably did research. That research lives as a temporal artefact:

```
_Temporal/Research/20260321-jwt-refresh-strategies.md
```

The research doc captures findings at a point in time — what you found, what sources you consulted, what conclusions you drew. The wiki page synthesises this into lasting reference. The research doc stays as historical record.

### Projects Tie Everything Together

When you're working on something with many moving parts, a project index keeps it all connected:

```
Projects/My App.md
```

```yaml
---
type: living/project
tags:
  - project/my-app
---
```

Every artefact related to this project shares the `project/my-app` tag. The project index links to the key pieces — designs, research, plans — but the tag lets you find everything, even things you forgot to link directly.

---

## Adding Attachments Without Vault Filesystem Access

An agent that has Brain MCP access can add a non-markdown file without receiving
general write permission to the vault:

```text
attachment.upload(
  destination_key="design/my-app-architecture",
  name="architecture.svg",
  content_base64="<base64 bytes>"
)
```

Brain resolves the active artefact and writes
`_Assets/Attachments/design~my-app-architecture/architecture.svg`, returning
the matching Obsidian embed. Pass the equivalent `design~my-app-architecture`
form if preferred. For temporal or shared assets, pass a bare standalone folder
key instead. With scripts or the thin CLI, the same workflow can read a
caller-owned file:

```bash
brain upload-attachment --destination-key design/my-app-architecture \
  --file /path/to/architecture.svg --json
```

The operation is additive. Retrying identical bytes is safe; a filename that
already contains different bytes returns an error rather than overwriting the
attachment.

When a living artefact's canonical key changes, Brain moves its attachment
scope and rewrites explicit embeds. Deletion or conversion to a temporal type
preserves the old scope and reports it as orphaned for deliberate cleanup.

## How the Brain Helps Agents Help You

The Brain isn't just for you — it's designed so that AI agents can understand your vault and work with it effectively.

### Agents Know Where Things Go

Because every type has a defined folder, naming pattern, and frontmatter schema, agents don't have to guess. They create files in the right place with the right structure, every time.

### Agents Find What's Relevant

The Brain provides search tools that let agents find existing work before creating new work. When you ask about something, the agent can surface related wiki pages, past research, previous decisions — context you might have forgotten.

### Agents Follow Your Triggers

The router (`_Config/router.md`) defines workflow triggers — things that should happen at certain moments. "After meaningful work, log it." "Before complex work, write a plan." Agents follow these automatically, so the vault stays maintained without you having to think about it.

### Agents Keep Your Vault Healthy

The Brain includes a structural compliance checker (`check.py`) that validates every file against its type's rules — naming patterns, frontmatter fields, empty folders, archive metadata, status values. Run it on demand to catch drift before it accumulates.

When compliance detects shaped drift — including valid parent metadata whose
folder projection was changed out-of-band in Obsidian — it points at the exact
repair command. `brain artefact repair --request-json '{"scope":"ownership"}' --dry-run --json`
previews the metadata-authoritative move set; apply it explicitly after review.
Brain never infers a missing parent field from folder structure. Lifecycle moves
prune the owner folders they vacate; `vault.check` reports any that remain as
`info` findings, and the `empty_folders` repair scope clears them.

### Agents Edit Without Losing Concurrent Changes

Agents first read an artefact or editable named resource, then supply that
read's exact revision to `document.write-body`, `document.replace-text`, `document.structured-edit`, or
`document.update-frontmatter`. If another editor changes the file first, Brain
rejects the stale mutation and directs the agent to re-read rather than silently
overwriting the intervening work.

### Agents Read Your Preferences

Your standing instructions and gotchas travel with the vault. Every agent session starts by reading them. Your preferences persist even when the conversation doesn't.

### Cookies

When an agent does good work, you can award a cookie. Cookies are temporal artefacts that track what was done, what made it satisfying, and why it earned one. Over time, the cookie log becomes a signal of what kinds of work land well — a feedback loop that helps agents understand what you value.

Agents are encouraged to ask honestly after meaningful work: "Was that good enough to earn a cookie? Because you know I'd do aaaanything for a cookie, so be straight with me." The value comes from cookies being genuine, not fished for.

### Archive an artefact

Use `artefact_archive` in MCP (or `brain artefact archive`) to remove any artefact from active use, including a Thought with no status. Archive preserves its lifecycle status; `artefact_unarchive` restores it. Active listing and lexical search refresh after archive, restore and deletion. Permanent `artefact.delete` requires Administrator authority and is absent from lower-ceiling callable catalogues.

### Recovering a blocked write

For a stale-router error, follow its `next_action` to `runtime.refresh-router`.
Retry the content operation only after repair succeeds. `force` requests an
unconditional rebuild and is normally unnecessary. A partial result means some
work committed: inspect the result before retrying. Lifecycle moves maintain
router and listing indexes themselves; no manual refresh between moves is needed.

`runtime.status` records warm-up progress. Its `ready` state is not proof that
caches are still current; use its `router_check` action for a blocked write.
