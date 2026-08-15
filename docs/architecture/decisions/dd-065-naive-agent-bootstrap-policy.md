# DD-065: Naive agent bootstrap policy

**Status:** Implemented (v0.59.1)
**Extends:** DD-038

## Context

DD-038 established three bootstrap tiers: MCP `session.start`, the generated
`.brain/local/session.md` mirror, and a raw-file fallback routed through
`.brain-core/md-bootstrap.md` for agents with none of the above. It named the
third tier but did not say how its content should be authored.

After several months of command-surface change, an audit of the tier-3 layer
found it had drifted in ways that were individually small and collectively
disqualifying:

- Ten of fourteen living artefact types omitted `key:` from their taxonomy
  frontmatter example, while `check.py` requires a valid key on **every** living
  artefact at severity `error`. An agent authoring from shipped markdown alone
  reliably produced non-compliant artefacts.
- `session-core.md` — holding the artefact model, the system principles, and the
  standards index — was unreachable from any tier-3 entry point.
- `standards/keys.md`, which states the key contract, was absent from both indexes
  that could have led to it.
- `guide.md` advertised four command families (`memory.*`, `skill.*`, `style.*`,
  `template.*`) that do not exist in the catalogue.

Each defect shipped. None was caught, because nothing verified the tier-3 layer.

Two further pressures shaped the response. The naive path is now legacy: normal
operation is scripts/CLI with optional MCP, and the project is moving toward remote
vaults reached through tooling, which the naive model cannot serve. And the layer
is shared — `session-core.md` is compiled into the MCP payload, so content added
for tier-3 agents is paid for by every agent on every session.

## Decision

The naive path is maintained for **correctness, not capability**, and scoped to
vault-local agents. It is governed by an explicit policy in
`docs/standards/naive-agent-bootstrap.md` and enforced mechanically by
`tests/test_naive_bootstrap.py`.

Five rules:

1. **Tooling-owned versus agent-owned.** Any field tooling injects must appear in
   the type's taxonomy frontmatter example and be named by an `{{agent: ...}}` hint
   in its template. The taxonomy is the contract; the hint is the reminder.
2. **Hints are self-terminating** and live in the body, never in frontmatter. A
   leaked token is caught by `check.py`'s `authoring_hint_tokens` rule.
3. **Scripts document behaviour; markdown documents contracts.** A naive agent
   cannot run a script but can read one, so a well-docstringed script is a
   legitimate reference target for *what the system does*. Contracts — naming,
   frontmatter, required fields — stay in markdown, because they are what the agent
   must produce.
4. **Naive routing is contained** to `md-bootstrap.md`. Docs shared with tiers 1
   and 2 are not contorted to serve tier 3.
5. **Every document has an exhaustive index.** Every standard is listed in
   `standards/README.md`, and `md-bootstrap.md` routes to that exhaustive index.
   `session-core.md` stays curated to avoid taxing every session. Every installed
   type has a taxonomy file whose declared folder exists.

## Alternatives

**Frontmatter placeholders instead of body hints.** Putting `key: {key}` directly in
template frontmatter would make the required field positionally obvious rather than
described in prose. Rejected on a hard technical constraint: YAML reads `{...}` as a
flow mapping, so `key: {key}` parses as a nested dict rather than a placeholder
string, and `key: {key}-suffix` is a parse error. Quoting survives parsing but makes
the placeholder indistinguishable from a real value and turns correctness into a
quote-dependent convention. This is why `artefact-library/README.md` already
confined hints to the body; the rule was sound and is now explained.

**Restating script behaviour in markdown.** Rejected as a drift generator. Prose
restating `compile_colours.py`'s four-step algorithm would rot independently of the
algorithm; the docstring is the better document and the only source of truth.

**A shipped type catalogue file.** Enumerating installed types in a markdown index
duplicates knowledge the filesystem already encodes and drifts the moment a type is
added. `md-bootstrap.md` routes to `_Config/Taxonomy/` instead, which is always
accurate and free to maintain.

**Retiring the naive path.** Rejected for now: it is the original operating model,
costs little once correct, and its absence would be a silent capability loss for
vault-local agents. Scoping it as legacy bounds the maintenance burden without
removing the capability.

## Consequences

Authoring a new artefact type now carries an explicit obligation: show every
tooling-injected field in the taxonomy example and add a self-terminating hint to
the template. `tests/test_naive_bootstrap.py` fails the build otherwise.

The catalogue check needs a small `KNOWN_NON_COMMANDS` allow-list, because
launcher-owned commands sit outside the application catalogue and some dotted
tokens in prose are field names rather than commands. The list is a maintenance
surface, kept short and justified inline.

Tier 3 gains a stated ceiling. Requests to extend the naive path with capability
rather than correctness now have a documented answer, and the containment rule (R4)
gives a principled basis for declining changes that would tax tiers 1 and 2.

`check.py` acquires a warning-severity rule with no repair counterpart yet; leaked
hint tokens are reported but must be removed by hand.
