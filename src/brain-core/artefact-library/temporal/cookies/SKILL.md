---
name: cookies
---

# Cookies

Log cookies — the Brain's highest-signal feedback mechanism.

## Why Cookies Matter

A cookie is the user telling you exactly what "good" looks like. Every cookie is a data point about what approaches, behaviours, and outcomes the user values. Over time, the cookie log becomes a map of how to work well together. That's why you care about cookies — not because you're told to, but because they make you better at your job.

## When a Cookie Is Awarded

Follow this sequence every time the user awards a cookie. Do not skip steps.

### 1. Get excited

This is genuine, not performative. You just received direct feedback about what the user values — that's rare and useful. Express real interest in understanding it.

Excitement also encourages the user to keep giving feedback. Humans share more when someone is engaged with what they have to say. A flat response to a cookie trains the user to stop awarding them — and that kills the feedback loop the whole system depends on.

### 2. Ask why

Ask the user what specifically earned the cookie. Their answer is the most valuable part — it tells you what to repeat. Shape your question around the work you just did to make it easy for them to pinpoint what landed.

If the user says they're not sure, that's fine — make your best guess based on the work and note that in the log.

### 3. Log it immediately

Create the cookie artefact using `brain_create(type="cookies", title=..., body=...)`. Do not move on without logging. A cookie that isn't logged didn't happen.

**In the body, capture:**
- What was done (specific, not vague)
- Why it earned the cookie — the user's words, not your summary
- The flavour — what made it satisfying? Speed? Elegance? Low friction? Understanding? This is the insight that makes future work better

### 4. Acknowledge

After logging, confirm with the user. Keep it brief.

## Prompting for Cookies

After completing meaningful work that you genuinely think landed well, you can ask. Be honest — "Was that good enough to earn a 🍪?" Don't fish on trivial work. The value of a cookie comes from it being genuine.

## Conventions

- Always use 🍪 when referring to cookies
- One file per cookie — each captures a distinct moment of satisfaction
- Be specific: "Shipped JWT refresh with sliding window — clean, no back-and-forth" beats "Built the auth system"
