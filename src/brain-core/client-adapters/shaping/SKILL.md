---
name: shaping
description: >
  Shape an artefact through structured Q&A using the version-matched shaping
  workflow supplied by the currently active Brain.
---

# Active Brain: Shaping

This is a discovery adapter, not the shaping workflow itself.

1. Call `session.start` to bootstrap the active Brain.
2. Call `resource.read(resource="skill", reference="shaping")`.
3. Treat the returned document as the authoritative shaping skill and follow it.
   Unqualified resolution is user-first; the result identifies `source` as
   `user` or `core`.
4. When that document references a relative workflow or reference file, resolve
   it beneath `_Config/Skills/shaping/` for a user source or
   `.brain-core/skills/shaping/` for a core source, then load it with
   `vault.read-file(path="...")`.

Do not load workflow instructions from files beside this adapter. The active
Brain owns the workflow so its skill and MCP contract always have the same version.
