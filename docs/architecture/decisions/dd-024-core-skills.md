# DD-024: Core Skills in `.brain-core/skills/`

**Status:** Implemented (v0.10.0)

## Context

Skills teach agents how to perform specific workflows. Some skills describe how to use brain-core's own tools — these need to ship with brain-core and stay current with the system. Placing them in `_Config/Skills/` alongside user skills would make them user-editable and not reliably upgraded.

## Decision

System-provided skills live in `.brain-core/skills/*/SKILL.md`. They are discovered by the compiler alongside user skills from `_Config/Skills/` and tagged `"source": "core"` in the compiled router (user skills tagged `"source": "user"`). Core skills are overwritten on upgrade — this is intentional, as they describe system methodology.

## Consequences

- Core skills are always current with the brain-core version they ship with.
- Users cannot accidentally corrupt core skills by editing `_Config/Skills/`.
- If a user wants to customise a core skill's behaviour, they create a user skill with the same name — the naming convention determines which takes precedence.
- The compiler must handle both `.brain-core/skills/` and `_Config/Skills/` discovery paths and correctly tag each source.
