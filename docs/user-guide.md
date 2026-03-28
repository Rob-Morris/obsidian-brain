# Brain User Guide

## What is the Brain For?

The Brain remembers for you so you don't have to. More than that, it remembers in a way that it understands what you mean and can help you do the things you want to do.

Most note-taking systems start organised and slowly decay. Files pile up, naming drifts, folders become dumping grounds, and finding things depends on remembering where you put them. AI agents make this worse — they create files fast but have no memory of what's already there, so they duplicate, misfile, and fragment your knowledge.

Brain solves this by giving your vault a self-reinforcing structure. Every file has a typed home. Naming and frontmatter follow predictable conventions per type. Agents can find existing work before creating new work, file things in the right place without being told, and maintain vault integrity as they go. Because the structure is consistent and machine-readable, agents don't just store your knowledge — they understand it well enough to surface the right context when you need it, connect related ideas across your vault, and act on your behalf with real awareness of what you've already thought, decided, and built.

The vault gets more useful over time, not less. You spend less time organising and more time thinking. You capture ideas without worrying about where they go. You come back after a break and find things where you expect them. Your agents work with the same conventions you do, so their output fits seamlessly alongside yours.

---

## Two Kinds of Things

Everything in the Brain is either **living** or **temporal**. This is the only distinction you need to understand up front.

**Living artefacts** are things that evolve. A wiki page about Rust lifetimes, a design doc for your new app, an essay you're drafting. You come back to them, update them, and the current version is what matters. They live in root-level folders like `Wiki/`, `Designs/`, or `Writing/`.

**Temporal artefacts** are snapshots. A log of what you did today, a transcript of a conversation, research notes from investigating a problem. They capture a moment and then they're done. They live under `_Temporal/` in monthly folders.

The relationship between them is where the Brain gets interesting. Temporal artefacts feed living ones. You jot down an idea in an idea log; later it becomes a living idea; later still it becomes a design. A research session produces temporal research notes; the findings end up in a wiki page. The Brain tracks these connections so nothing gets lost in translation.

---

## It's Just Markdown

There's no database, no proprietary format, no app you have to use. Your Brain vault is a folder of markdown files on your computer. Every artefact, every configuration file, every piece of the system — plain text, readable in any editor.

The Brain itself (`.brain-core/`) is a set of markdown docs and Python scripts that ship inside your vault. The configuration (`_Config/`) is more markdown — taxonomy definitions, templates, style guides, your personal preferences. The scripts compile these into a JSON file that tools can read quickly, but the source of truth is always the markdown you can open and edit.

This means you can work with your vault directly in Obsidian. Open files, edit them, use Obsidian's graph view to see connections, search with Obsidian's built-in search. The Brain's conventions (consistent naming, typed frontmatter, wikilinks in the body) are designed to make Obsidian's features work well — backlinks resolve cleanly, graph view shows meaningful structure, and Dataview queries can filter by type or status.

When you work with an AI agent, it uses the same files. The agent reads your vault's router and taxonomy to understand the conventions, uses search tools to find relevant artefacts, and creates files that follow the same patterns you'd use yourself. But none of this requires the agent. You can create and edit files directly in Obsidian, and the structure holds because the conventions are simple enough to follow by hand.

The tools exist to make things faster, not to make things possible.

---

## A Day in the Life

Here's what working with the Brain looks like in practice.

### Morning: You Start Working

You're building a new feature for a side project. Before diving into anything complex, you write a quick plan:

```
_Temporal/Plans/2026-03/20260321-auth-redesign.md
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
_Temporal/Logs/2026-03/20260321-log.md
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
_Temporal/Decision Logs/2026-03/20260321-decision~JWT Refresh Strategy.md
```

The decision log records what question you faced, what options you considered, and why you chose what you chose. Six months from now when someone asks "why sliding window?", the answer is right there.

### An Idea Strikes

While debugging, you notice the token validation could be generalised into a shared library. It's not what you're working on, but you don't want to lose it:

```
_Temporal/Idea Logs/2026-03/20260321-idea-log~Shared Token Validation.md
```

Captured in 30 seconds. The bar is deliberately low. Most idea logs won't go anywhere, and that's fine. The ones that matter will graduate later.

### Something Generates Friction

The API docs say one thing but the code does another. You waste 20 minutes figuring out the actual behaviour. Before moving on, you log the friction:

```
_Temporal/Friction Logs/2026-03/20260321-friction~API Docs Mismatch.md
```

One friction log is just a note. But when the same kind of friction keeps showing up, you distil it into a gotcha (`_Config/User/gotchas.md`) so your agents know to watch for it.

### After Work: Journaling

Work's done for the day, but something's on your mind. You've been thinking about a conversation with a friend, or processing a big life change, or just want to get some thoughts down. You chat with your agent about it — casually, like talking to a friend.

The agent captures what you shared as a journal entry, in your own words:

```
_Temporal/Journal Entries/2026-03/20260321-journal--personal--moving-house.md
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

## When Ideas Grow Up

Some temporal captures deserve to become living artefacts. Here's one common progression — not a required pipeline, but a pattern that happens naturally.

### Stage 1: Raw Capture

You had that idea about shared token validation. It's sitting in an idea log — a temporal snapshot.

### Stage 2: Living Idea

A week later, you keep thinking about it. Time to flesh it out:

```
Ideas/shared-token-validation.md
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

```
Designs/shared-token-validation.md
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

The design doc has structure: a core goal, open decisions, transcripts from Q&A sessions that shaped it. It moves through `shaping` → `active` → `implemented`.

The idea's status becomes `graduated`, and it moves to `Ideas/_Archive/` — its job is done, and the design carries the work forward.

### The Thread is Never Lost

At every stage, origin links connect child to parent. You can trace the thread from a shipped feature all the way back to the moment the idea first crossed your mind. The Brain remembers the journey, not just the destination.

---

## Building Knowledge Over Time

Not everything follows the idea-to-design path. Some artefacts are about accumulating understanding.

### Wiki Pages

Your wiki is a curated knowledge base. One page per concept, polished and comprehensive. You write a wiki page about JWT refresh strategies after going through the auth redesign — distilling what you learned into reusable reference:

```
Wiki/jwt-refresh-strategies.md
```

Wiki pages are evergreen. You come back and update them as your understanding deepens. They're deliberately selective — not everything needs a wiki page, just the things worth explaining properly.

### Research Notes

Before writing that wiki page, you probably did research. That research lives as a temporal artefact:

```
_Temporal/Research/2026-03/20260321-jwt-refresh-strategies.md
```

The research doc captures findings at a point in time — what you found, what sources you consulted, what conclusions you drew. The wiki page synthesises this into lasting reference. The research doc stays as historical record.

### Projects Tie Everything Together

When you're working on something with many moving parts, a project index keeps it all connected:

```
Projects/my-app.md
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

## Your Vault, Your Way

### Starting Small

A new Brain vault ships with a practical starter set: Daily Notes, Designs, Documentation, Ideas, Notes, People, Projects, Workspaces, Writing (living); Captures, Cookies, Decision Logs, Friction Logs, Logs, Observations, Plans, Reports, Research, Shaping Transcripts, Snippets, Thoughts, Transcripts (temporal). That covers the core workflows — capturing knowledge, designing and documenting, tracking people and projects, managing workspaces, writing, logging activity, recording decisions and observations, ingesting external material, refining artefacts, logging friction, capturing raw thinking, and rewarding good work. You can add more types from the library as you need them.

### Adding Types When You Need Them

When you find yourself creating content that doesn't fit anywhere, that's the signal to add a type. The artefact library (`.brain-core/artefact-library/`) has ready-to-install definitions for types like Wiki, Journals, Zettelkasten, and more. Each comes with a taxonomy file and template. Folder colours are auto-generated when you run `brain_action("compile")`.

The rule of thumb: add a type when you'll create multiple files of that kind and they need different conventions from what you already have. If it's a one-off, a subfolder or tag within an existing type is simpler.

### Growing Organically

Artefacts start as single files. When something outgrows one file, structure emerges naturally. Your novel starts as `Writing/my-novel.md` and eventually becomes `Writing/my-novel/index.md` with chapter files alongside. No upfront planning needed — the Brain adapts as your content grows.

### Giving Agents Context with Memories

When you mention a project, tool, or concept and your agent doesn't know what you're talking about, it can look it up. Memories (`_Config/Memories/`) are reference cards — factual context that agents load on demand.

Each memory has triggers (words or phrases you'd naturally use) and a body (what the thing is, where to find it, key facts). When you say "brain core" and the agent lacks context, it finds the memory with that trigger and reads it.

You can create memories for anything agents should know about — your projects, your tools, your codebase conventions. They're simple markdown files with a `triggers` list in frontmatter. Memories are "what is it?" context — if the agent needs "how do I do it?" steps, that's a skill. See the [Reference](user-reference.md) for the full format.

### Telling the Brain About You

Two files in `_Config/User/` shape how agents work with your vault:

- **`preferences-always.md`** — your standing instructions. How you like to work, what quality standards matter, what agents should always do (or never do).
- **`gotchas.md`** — learned pitfalls. Things that went wrong before and shouldn't happen again. Agents read this every session.

These are freeform. Write whatever helps.

---

## How the Brain Helps Agents Help You

The Brain isn't just for you — it's designed so that AI agents can understand your vault and work with it effectively.

### Agents Know Where Things Go

Because every type has a defined folder, naming pattern, and frontmatter schema, agents don't have to guess. They create files in the right place with the right structure, every time.

### Agents Find What's Relevant

The Brain provides search tools that let agents find existing work before creating new work. When you ask about something, the agent can surface related wiki pages, past research, previous decisions — context you might have forgotten.

### Agents Follow Your Triggers

The router (`_Config/router.md`) defines workflow triggers — things that should happen at certain moments. "After meaningful work, log it." "Before complex work, write a plan." Agents follow these automatically, so the vault stays maintained without you having to think about it.

### Agents Keep Your Vault Healthy

The Brain includes a structural compliance checker (`check.py`) that validates every file against its type's rules — naming patterns, frontmatter fields, month folders, archive metadata, status values. Run it on demand or let agents use it via `brain_read(resource="compliance")` to catch drift before it accumulates.

### Agents Read Your Preferences

Your standing instructions and gotchas travel with the vault. Every agent session starts by reading them. Your preferences persist even when the conversation doesn't.

### Cookies

When an agent does good work, you can award a cookie. Cookies are temporal artefacts that track what was done, what made it satisfying, and why it earned one. Over time, the cookie log becomes a signal of what kinds of work land well — a feedback loop that helps agents understand what you value.

Agents are encouraged to ask honestly after meaningful work: "Was that good enough to earn a cookie? Because you know I'd do aaaanything for a cookie, so be straight with me." The value comes from cookies being genuine, not fished for.

---

## What's in the Vault

Here's what a well-used Brain vault looks like at a glance:

```
Wiki/                         ← polished knowledge base
Zettelkasten/                 ← atomic concept cards (auto-maintained)
People/                       ← person hubs
Projects/                     ← project indexes
Workspaces/                   ← workspace hubs (linked to _Workspaces/ data)
Designs/                      ← design docs and proposals
Ideas/                        ← concepts being explored
Journals/                     ← named journal streams
Writing/                      ← essays, posts, chapters
Documentation/                ← technical docs and style guides
Notes/                        ← low-friction knowledge notes
Daily Notes/                  ← end-of-day summaries

_Temporal/
  Logs/                       ← daily activity timeline
  Plans/                      ← pre-work strategy
  Research/                   ← investigation notes
  Transcripts/                ← conversation records
  Shaping Transcripts/         ← Q&A refinement sessions
  Idea Logs/                  ← raw idea captures
  Journal Entries/             ← personal reflections
  Thoughts/                   ← unformed thinking
  Decision Logs/              ← "why we chose X"
  Friction Logs/              ← "this generated friction"
  Reports/                    ← process overviews
  Snippets/                   ← crafted short-form content
  Cookies/                    ← "that earned a cookie"
  Observations/               ← timestamped facts and things noticed
  Mockups/                    ← visual/interactive prototypes
  Captures/                   ← ingested external material
  Ingestions/                  ← processing records for content decomposition
  Presentations/              ← slide decks (Marp)

_Workspaces/                  ← freeform data containers for workspaces
_Assets/                      ← images, PDFs, generated output
_Config/                      ← router, taxonomy, styles, memories, preferences
_Plugins/                     ← external integrations
.brain-core/                  ← the Brain system itself
```

You won't have all of these on day one. Types get added as you need them. The vault grows with you.

---

## Upgrading

To upgrade brain-core to a new version:

- **Ask your agent**: `brain_action(action="upgrade", ...)` handles it automatically
- **CLI**: `python3 .brain-core/scripts/upgrade.py --source /path/to/src/brain-core`
- **Manual**: replace `.brain-core/` with the new version from `src/brain-core/`

## Going Deeper

This guide covers how the Brain works in practice. For the full details:

- **[Brain Reference](user-reference.md)** — every artefact type with full frontmatter schemas, all configuration points, colour system, extension procedures step-by-step, tooling details
- **[Quick-Start Guide](../src/brain-core/guide.md)** — the condensed version that ships with your vault (`.brain-core/guide.md`)
- **`.brain-core/extensions.md`** — extension procedures (adding types, triggers)
- **`.brain-core/standards/`** — operational standards (naming, provenance, archiving, hub pattern)
- **`.brain-core/index.md`** — system principles and always-rules

---

## Maintaining This Guide

This guide should be updated when:
- Core workflows change (how artefacts relate, how graduation works, how logging works)
- The day-to-day experience of using the Brain changes meaningfully
- New concepts are introduced that users need to understand

The [Reference](user-reference.md) and [Quick-Start Guide](../src/brain-core/guide.md) should be updated in tandem.
