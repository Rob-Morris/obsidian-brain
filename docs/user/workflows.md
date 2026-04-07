# Brain Workflows

Day-to-day usage patterns for working with the Brain.

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

## Working with Tasks and Notes

**Tasks** (`Tasks/`) are persistent units of work — things you're tracking across sessions, not one-off to-dos. They have status values (`open`, `in-progress`, `done`, `blocked`) and link to related artefacts.

**Notes** (`Notes/`) are low-friction knowledge captures — things you want to record but that don't need the structure of a wiki page. Write first, organise later. A note can always be promoted to a wiki page when it earns it.

The daily note's task list is a digest of what happened, not the authoritative record. Authoritative task status lives on the task artefact itself.

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

The idea's status becomes `adopted`, and `brain_edit` automatically moves it to `Ideas/+Adopted/` — its job is done, and the design carries the work forward. Wikilinks update vault-wide. If an idea is later revived (status set back to non-terminal), it moves back out.

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
