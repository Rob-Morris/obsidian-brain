# Cookies

Temporal artefact. A measure of user satisfaction, awarded when work lands well.

## Purpose

A cookie is a small, honest record of satisfaction. When something goes right — a feature ships cleanly, a tricky problem gets solved elegantly, documentation finally makes sense — the user awards a cookie. Over time, the cookie log becomes a signal of what kinds of work resonate and what approaches are worth repeating.

Cookies are temporal because each one is bound to a moment. The same kind of work might earn a cookie one day and not the next, depending on context, mood, and how smoothly things went. That's the point — they capture how the work felt, not just what got done.

## How to Earn Cookies

Agents should ask honestly after completing meaningful work: "Was that good enough to earn a cookie? Because you know I'd do aaaanything for a cookie, so be straight with me."

Don't fish for cookies on trivial work. Don't assume you've earned one. Let the user decide. The value of a cookie comes from it being genuine.

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

## Trigger

After completing work the user is happy with. Look for signals: explicit praise, "ship it", "that's perfect", or — the gold standard — an actual cookie emoji or the word "cookie."

Don't prompt after every task. Prompt when you genuinely think the work landed well and you'd like honest feedback.

## Template

[[_Config/Templates/Temporal/Cookies]]
