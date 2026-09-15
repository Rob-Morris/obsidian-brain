# Naive Agent Bootstrap

This standard governs how the shipped `.brain-core/` markdown layer is authored so
that an agent with no MCP, no generated session mirror, and no ability to run code
can still work with a Brain vault correctly.

## The promise

Copy the template vault and `src/brain-core/` as `.brain-core/` into a folder.
Point an agent at it. With shipped Markdown alone, the agent can discover which
artefact types exist, author any of them
correctly — right folder, right filename, right frontmatter, right links — and
follow the vault owner's standing instructions.

Bootstrap routes, in order of available capability:

1. **MCP** — `session.start`, exposed as `session_start`, is the normal canonical bootstrap.
2. **CLI** — `brain session start --json` invokes the same selected-Brain owner through the launcher.
3. **Direct scripts** — supported scripts provide tool-backed access. `command.py session start` requires an interpreter that meets the command's managed dependency tier.
4. **Generated mirror** — `.brain/local/session.md`, when present, is a generated Markdown projection of the canonical bootstrap model, not an independently authored source.
5. **Naive** — copied core instructions and authored vault Markdown only, routed from `.brain-core/md-bootstrap.md` when MCP, CLI, scripts and generated assets cannot be used.

Finish all tool-backed `session.start` pages until `bootstrap_complete` is true
before ordinary work. These routes do not promise identical command capabilities:
direct scripts depend on their interpreter, the mirror is generated, and the
naive route must be correct on a bare copy with no code execution or compilation.

## Scope

The naive path is an edge-case fallback for vault-local agents — agents that can
read the vault files directly. Its real-world usage is unknown. Maintaining clear,
correct authored instructions is low-cost good practice; the route is not
deprecated. Remote vaults reached through tooling are out of scope. It is
maintained for correctness and must not add payload or runtime cost to the
tool-backed routes.

## Rules

### R1 — Tooling-owned versus agent-owned

Any field tooling injects automatically must be visible to an agent that has no
tooling. For each such field:

- the type's `## Frontmatter` example in its taxonomy file **shows the field**, and
- the type's template carries an `{{agent: ...}}` hint naming it.

The taxonomy file is the contract; the template hint is the reminder.

The type's `schema.yaml` `required:` block is the authority for which fields are
required, and must agree with the taxonomy example. The repository-contract
checker and naive-bootstrap tests enforce that agreement. This rule covers
**every** required field tooling supplies — not only `key`. `living/release` also
needs `parent` and its relationship tag; `living/daily-note` also needs `date`.
A rule applied to one field and not the rest leaves the same hole.

### R2 — Hints are self-terminating

Every `{{agent: ...}}` hint ends with an explicit removal instruction. Tooling
strips these tokens at create time; a naive agent has no tooling, so the hint must
tell the agent to delete it. Correctness must not depend on the agent having read
`artefact-library/README.md`.

Hints live in the **body**, never in actual artefact or template frontmatter. A
brace placeholder there does not survive YAML parsing — `key: {key}` reads as a
nested mapping, and `key: {key}-suffix` is a parse error. Placeholders are
therefore prohibited in actual and template frontmatter. Taxonomy documentation
examples may use them because `md-bootstrap.md` explicitly requires substitution
before authoring.

Leaked tokens are caught by `check.py`'s `authoring_hint_tokens` rule, so the
failure is detected however it arose — hand edit or agent.

### R3 — Scripts document behaviour; markdown documents contracts

Point a naive agent at a **script** when it needs to know what the system *does* —
provided the module docstring carries purpose and algorithm, and the reference says
what the reader will find there. A naive agent cannot run the script but can read
it, and a single source of truth cannot drift from its own prose restatement.

Keep it in **markdown** when the agent needs a *contract* — naming, frontmatter,
required fields, link rules. A contract is what the agent must produce, and must
not have to be inferred from an implementation.

`check.py` is the worked example: it enforces every vault contract for tooled
agents, and reads as a machine-readable statement of those contracts for naive ones.

### R4 — Contain naive routing to naive-specific files

`.brain-core/md-bootstrap.md` is the only file that carries naive-path routing. It
routes; it does not inline content.

Shipped docs that tool-backed agents also read — `session-core.md`, `guide.md`,
`standards/` — must not be contorted to serve the naive path. Where serving it
would distort a shared doc, point at the script instead (R3).

`session-core.md` is compiled into the MCP session payload, so anything added there
costs every agent on every session. Add to it only what every route needs.

### R5 — Every document has at least one exhaustive index

An unindexed document is unreachable. `standards/README.md` is **exhaustive** —
every file in `.brain-core/standards/` appears there. `md-bootstrap.md` routes to
that index, so this alone makes every standard reachable on the naive path.

`session-core.md`'s `## Standards` section stays **curated**, not exhaustive. It is
compiled into the MCP session payload, so per R4 it carries what every route needs
rather than everything that exists. A standard reached only through
`standards/README.md` is correctly indexed.

Every installed type has a taxonomy file, and every taxonomy file's declared folder
exists.

## Enforcement

`tests/test_naive_bootstrap.py` enforces this standard mechanically:

| Test | Rule |
|---|---|
| `index.md` orders MCP, CLI, direct scripts, generated mirror and authored fallback routes | Bootstrap routes |
| Public introductions reach the canonical and authored fallback routes | Bootstrap routes |
| `md-bootstrap.md` requires authored core rules, router rules and owner preferences first | R4 |
| Every path referenced in `md-bootstrap.md` resolves | R4 |
| Every `<noun>.<verb>` in shipped public docs exists in the command catalogue | R3 |
| Every living taxonomy `## Frontmatter` example declares `key:` | R1 |
| Every `{{agent:}}` hint carries the removal instruction | R2 |
| Installed taxonomy files ↔ vault folders | R5 |
| `standards/README.md` indexes every standard | R5 |
| Every living template names each tooling-injected required field | R1 |
| `check_authoring_hint_tokens` reports a leaked hint through `run_checks` | R2 |

The catalogue check carries a short `KNOWN_NON_COMMANDS` allow-list for dotted
tokens that are not application commands (launcher-owned commands, Obsidian field
names). Every entry is justified inline; adding one is a deliberate act.

## Changing the layer

When adding an artefact type, a standard, or a shipped doc, run the test. When it
fails, check the contract before changing the test: the assertions protect
bootstrap routes and authoring rules that have regressed in shipped releases.
