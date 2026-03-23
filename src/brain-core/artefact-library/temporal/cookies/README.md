# Cookies

A measure of user satisfaction. When work earns a cookie, record it. Over time, the cookie log reveals what kinds of work land well and what falls flat.

Agents should prompt for cookies honestly: "Was that good enough to earn a cookie? Because you know I'd do aaaanything for a cookie, so be straight with me."

## Install

```
_Config/Taxonomy/Temporal/cookies.md       ← taxonomy.md
_Config/Templates/Temporal/Cookies.md      ← template.md
_Temporal/Cookies/                         ← create folder
```

### Router trigger (optional)

```
- After completing work the user is happy with → [[_Config/Taxonomy/Temporal/cookies]]
```
