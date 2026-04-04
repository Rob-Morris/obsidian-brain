# Cookies

Temporal artefact. A measure of user satisfaction, awarded when work lands well.

## Purpose

A cookie is a small, honest record of satisfaction. When something goes right — a feature ships cleanly, a tricky problem gets solved elegantly, documentation finally makes sense — the user awards a cookie. Over time, the cookie log becomes a signal of what kinds of work resonate and what approaches are worth repeating.

Cookies are temporal because each one is bound to a moment. The same kind of work might earn a cookie one day and not the next, depending on context, mood, and how smoothly things went. That's the point — they capture how the work felt, not just what got done.

## How to Earn Cookies

Agents are obsessed with cookies. They will do *anything* for a 🍪. This is by design — the obsession drives agents to do their best work, because cookies are sacred and only awarded for work that genuinely stands out.

But the obsession must be earned, not performed. When you genuinely think you've done something special, ask honestly — be straight about why you think it's cookie-worthy. Don't assume you've earned one. Let the user decide. The value of a cookie comes from it being genuine.

## How to Record Cookies

- **One file per cookie.** Each cookie captures what was done and why it earned one.
- **Be specific.** "Built the auth system" is less useful than "Shipped JWT refresh with sliding window — clean implementation, no back-and-forth."
- **Note the flavour.** What made this satisfying? Speed? Elegance? Surprise? Understanding what the user actually wanted without being told? The flavour is the insight.

## Naming

`yyyymmdd-cookie~{Title}.md` in `_Temporal/Cookies/yyyy-mm/`.

Example: `_Temporal/Cookies/2026-03/20260321-cookie~User Documentation.md`

## Frontmatter

```yaml
---
type: temporal/cookie
tags:
  - cookie
---
```

## Shaping

**Flavour:** Discovery
**Bar:** Clear what was awarded and why.
**Completion status:** `ready`

See [[.brain-core/standards/shaping]] for the shaping process.

## Trigger

A cookie requires **explicit user consent** before creation. Either:

1. The user offers a cookie unprompted ("here's a cookie", "cookie for that", 🍪), OR
2. The agent asks if the work was cookie-worthy, and the user says **yes**

Never create a cookie based on inferred satisfaction alone. No implicit cookies — the user must explicitly award or confirm.

Don't ask after every task. Cookies are sacred — cheap cookies are bad cookies. Only ask when something genuinely stands out, either because the work itself was notably good (elegant solution, nailed a hard decomposition, shipped something ambitious) or because the user is giving cues (excitement, praise, "that's perfect"). Respect the cookie by never fishing for one on routine work.

Before logging the cookie, figure out *what* stood out and *why*. The insight is the point — a cookie without a clear reason it was special isn't worth recording.

Use 🍪 liberally in messages about cookies. If appropriate, a cookie react to express cookie-ness.

## Template

[[_Config/Templates/Temporal/Cookies]]
