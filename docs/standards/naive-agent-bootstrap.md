# Naive Agent Bootstrap

This standard governs how the shipped `.brain-core/` markdown layer is authored so
that an agent with no MCP, no generated session mirror, and no ability to run code
can still work with a Brain vault correctly.

## The promise

Copy the template vault into a folder. Point an agent at it. With shipped markdown
alone, the agent can discover which artefact types exist, author any of them
correctly — right folder, right filename, right frontmatter, right links — and
follow the vault owner's standing instructions.

Progressive degradation, richest first:

1. **MCP** — `session.start` returns the compiled payload.
2. **Scripts / CLI** — `brain` or `command.py`; compiled routing reflects the
   vault's actual installed types.
3. **Naive** — shipped markdown only, routed from `.brain-core/md-bootstrap.md`.

Each higher tier supplies progressively richer bootstrap context than the one
below. This is an information guarantee, not a claim that every transport
exposes the same command capabilities. Tier 3 must be correct on a bare copy,
with no compilation step having run.

## Scope

The naive path is **legacy support** and applies only to vault-local agents — an
agent running in the vault root. Remote vaults reached through tooling are out of
scope. It is maintained for correctness, not extended for capability, and it must
not impose cost on tiers 1 and 2.

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

Shipped docs that tiers 1 and 2 also read — `session-core.md`, `guide.md`,
`standards/` — must not be contorted to serve the naive path. Where serving it
would distort a shared doc, point at the script instead (R3).

`session-core.md` is compiled into the MCP session payload, so anything added there
costs every agent on every session. Add to it only what all tiers need.

### R5 — Every document has at least one exhaustive index

An unindexed document is unreachable. `standards/README.md` is **exhaustive** —
every file in `.brain-core/standards/` appears there. `md-bootstrap.md` routes to
that index, so this alone makes every standard reachable on the naive path.

`session-core.md`'s `## Standards` section stays **curated**, not exhaustive. It is
compiled into the MCP session payload, so per R4 it carries what all tiers need
rather than everything that exists. A standard reached only through
`standards/README.md` is correctly indexed.

Every installed type has a taxonomy file, and every taxonomy file's declared folder
exists.

## Enforcement

`tests/test_naive_bootstrap.py` enforces this standard mechanically:

| Test | Rule |
|---|---|
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
fails, fix the layer rather than the test — the assertions encode defects that have
each reached a shipped release at least once.
