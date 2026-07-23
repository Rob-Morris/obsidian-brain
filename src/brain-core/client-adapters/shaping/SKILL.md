---
name: shaping
description: >
  Shape an artefact through structured Q&A using the version-matched shaping
  workflow supplied by the currently active Brain.
---

# Active Brain: Shaping

This is a discovery adapter, not the shaping workflow itself.

1. Call `brain_session` to resolve and bootstrap the active Brain.
2. Call `brain_read(resource="file", name=".brain-core/skills/shaping/SKILL.md")`.
3. Treat the returned document as the authoritative shaping skill and follow it.
4. When that document references a relative skill file, resolve it beneath
   `.brain-core/skills/shaping/` and load it with `brain_read(resource="file", name="...")`.

Do not load workflow instructions from files beside this adapter. The active
Brain owns the workflow so its skill and MCP contract always have the same version.
