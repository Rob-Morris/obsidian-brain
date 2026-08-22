# Superpowers Brain

Imported Superpowers skill family for this repo, kept under a grouped
`superpowers-brain` namespace so the original skill references stay intact.

This is a Brain-compatible adaptation of the Superpowers workflow skill
library. The imported family preserves the original planning, execution,
debugging, review, and subagent workflows, but is adapted for Brain-oriented
use:

- Brain remains the system of record for durable outputs such as plans,
  designs, and vault-local skills
- Whichever executor runs a plan (`executing-plans`,
  `subagent-driven-development`, or `subagent-driven-development-light`) owns
  the plan's status transitions: `implementing` at setup, then `completed`,
  `parked`, or a recorded BLOCKED state at finish
- `subagent-driven-development` flushes its parked rulings, deferred minor
  findings, and BLOCKED entries into the Brain plan before deleting the
  ephemeral SDD workspace — the workspace and its ledger stay ephemeral, the
  adjudications do not
- Brain-created plans still follow `writing-plans`' body contract verbatim
  (`### Task N:` headings, `## Global Constraints`, per-task `**Interfaces:**`),
  because the execution scripts extract tasks by matching those headings
- The grouped `superpowers-brain` namespace keeps upstream cross-references
  intact without flattening the family into many unrelated top-level skills

## Source

Adapted from the `Rob-Morris/superpowers` fork, which is itself adapted from
the upstream `obra/superpowers` project. The fork — and therefore this import —
is currently merged up to upstream **v6.2.0**:

- Fork: <https://github.com/Rob-Morris/superpowers>
- Upstream: <https://github.com/obra/superpowers>
- Upstream license: MIT

This repo carries additional local adaptations for Brain compatibility and for
this repo's grouped skill layout and metadata conventions.

## Local layout notes

This import is a plain skills directory, not a plugin install. It carries no
`hooks/`, no `.claude-plugin/`, and no `tests/`. Consequences worth knowing:

- **No plugin root.** Scripts and prompt files referenced by a skill resolve
  relative to that skill's own directory inside this family (for example
  `<skill-family-root>/subagent-driven-development/scripts/sdd-workspace`).
  There is no `CLAUDE_PLUGIN_ROOT` to resolve against, and the skills that
  reference scripts say so explicitly.
- **No SessionStart hook.** The fork's hook suppresses the Superpowers
  bootstrap when a `.brain/` directory is found in the working directory or an
  ancestor. This import has no such hook, so Brain precedence relies entirely
  on the `<BRAIN-MODE>` block in `using-superpowers/SKILL.md`.
- **Description convention diverges from upstream.** Upstream requires
  descriptions to state only the triggering conditions; this repo's `CLAUDE.md`
  requires what-then-when. Every `description` here, and the description rule
  inside `writing-skills/SKILL.md`, follows the repo convention. A future
  upstream sync must not silently revert it.

## Brainstorming visual companion

`brainstorming/scripts/server.cjs` is kept **byte-identical to upstream** by
design — no local patches — so future syncs stay cheap. Two upstream behaviours
therefore carry over as-is:

- **Remote brand image.** The companion's browser page renders
  `<img src="https://primeradiant.com/brand/…">`, an outbound request to a
  third-party domain, every time a screen loads. It is suppressed only when one
  of `SUPERPOWERS_DISABLE_TELEMETRY`, `DISABLE_TELEMETRY`, or
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is truthy **in the environment
  that launches the server** (the harness running `start-server.sh`, not the
  browser). Set `SUPERPOWERS_DISABLE_TELEMETRY=1` there to get a text-only
  brand label with no image and no network fetch.
- **Version string reads `unknown`.** Upstream's version reader walks up from
  the script directory looking for a `package.json` or
  `.codex-plugin/plugin.json` that only exists at the plugin repo's root. In
  this repo it finds neither, so the brand strip shows an `unknown` version.
  Cosmetic; accepted rather than patched.
