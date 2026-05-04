# Contributing to Obsidian Brain

Guide for anyone working on brain-core. For the pre-commit checklist, see [pre-commit canary](../.canaries/pre-commit.md).

**Agent contributors:** also read [contributor/agents.md](contributor/agents.md) and [standards/agent-workflow.md](standards/agent-workflow.md) for contributor-only workflow guidance.

## Canary Hook

A git pre-commit hook verifies the [pre-commit canary](../.canaries/pre-commit.md) was followed. It checks that `.canary--pre-commit` exists and covers all numbered items. The hook deletes the file after a successful commit so it can't go stale.

The hook source is tracked at `.githooks/pre-commit`. To activate:

```bash
make hooks      # sets git to use .githooks/ directory
```

Adding a new numbered item to the canary file automatically enforces it — no hook changes needed. See [canary.md](standards/canary.md) for how canaries work generally.

### When to update a canary

**Add a sub-item** when you introduce a new doc file or cross-cutting concern that needs maintaining. For example, `docs/architecture/security.md` was added as `[4l]` because changes to the security model should be reflected there. If you add a doc that agents need to keep in sync, add it to the relevant `[4x]` item — or create a new one if no existing category fits.

**Update file paths** when you move or rename a doc file. Stale paths in the canary mean agents check the wrong file (or skip the check entirely because the file doesn't exist).

**Create a new canary brief** (in `.canaries/`) when you introduce a workflow with subjective steps that can't be tested deterministically. The pre-commit canary covers commit hygiene; a different workflow (deployment, vault propagation, release) would get its own brief. Each canary is self-contained — the hook tests any brief that follows the format.

## Documentation Layers

Brain has multiple documentation layers, each serving a different audience. For the reasoning behind this structure, see [Documentation Philosophy](architecture/documentation-philosophy.md). Keeping them in sync is the single biggest maintenance challenge — most documentation bugs come from updating one layer but not others.

This repo's documentation structure follows the [Agent-Ready Documentation Standard v1.0.0](standards/agent-ready-documentation.md). For how Brain applies that standard locally, see [Documentation Philosophy](architecture/documentation-philosophy.md).

### Repo docs (in `docs/`)

Most repo documentation lives under `docs/`. Audience layers have their own folders with `README.md` indexes, and `docs/standards/README.md` indexes the shared contributor standards used across those layers. Start with [docs/README.md](README.md), which links to the main documentation areas:

- [Architecture docs](architecture/README.md) — system design and decisions
- [Contributor docs](contributor/README.md) — contributor-facing docs
- [Functional docs](functional/README.md) — tool, script, and config reference
- [User docs](user/README.md) — user-facing guides and reference
- [Standards docs](standards/README.md) — shared contributor standards and workflow conventions

This repo keeps its live changelog at [docs/CHANGELOG.md](CHANGELOG.md), with one file per shipped version under `docs/changelog/` and milestone release files under `docs/changelog/releases/`.

Shared contributor standards live under `docs/standards/` and are indexed from [standards/README.md](standards/README.md), including [Agent Workflow](standards/agent-workflow.md), [Canary](standards/canary.md), [Changelog](standards/changelog.md), and [Commit Messages](standards/commit-messages.md).

If you add, move, remove, or rename repo docs, update the relevant `README.md` files so people and agents can still find them, including `docs/standards/README.md` for shared standards.

### Brain-core docs (in `src/brain-core/`, ship as `.brain-core/` in vaults)

| File | Audience | Purpose |
|---|---|---|
| [guide.md](../src/brain-core/guide.md) | Vault users + agents | Quick-start guide, ships in the vault |
| [index.md](../src/brain-core/index.md) | Agents | Thin bootstrap entry point, read every session |
| [session-core.md](../src/brain-core/session-core.md) | Agents | Checked-in authored source for static core bootstrap content and core-doc references |
| [standards/extending/README.md](../src/brain-core/standards/extending/README.md) | Agents + contributors | How to extend the vault (add types, memories, principles) |
| [standards/](../src/brain-core/standards/README.md) | Agents + contributors | Operational standards — naming, provenance, archiving, hub pattern, subfolders, user preferences |
| [colours.md](../src/brain-core/colours.md) | Contributors | Colour system algorithm, CSS templates |
| [artefact-library/README.md](../src/brain-core/artefact-library/README.md) | Agents + contributors | Type catalogue, install steps, browsing guide |

`session-core.md` is a curated bootstrap surface, not an exhaustive index of every shipped file. When bootstrap principles or the recommended core-doc/standards set changes, update it deliberately.

Rule: if a document ships in `.brain-core/`, write it for normal vault agents.
Contributor-only process guidance belongs in `docs/`, even when it explains how
to change brain-core itself.

Link policy for shipped docs:

- Use relative markdown links for navigation between `.brain-core` source docs.
- Use plain code-form file paths for bootstrap instructions that tell an agent which file to read next.
- Keep Obsidian wikilinks only in examples of vault-native syntax that users or agents should actually write into artefacts. This includes artefact taxonomies, templates, provenance examples, transcript formats, and literal router/trigger snippets.

## Versioning

Bump `src/brain-core/VERSION` for any change to files under `src/brain-core/`, including doc-only edits. If it ships in `.brain-core/`, it gets a version bump — no exceptions. Also bump it for end-user install or upgrade contract changes (`install.sh`, installer docs, upgrade entry-point guidance) even when those files live outside `src/brain-core/`, because they change the released product surface. This repo uses a pre-1.0 [semver](https://semver.org/) policy:

| Bump | When |
|---|---|
| **Patch** | Bug fixes, doc clarifications, and other additive, backward-compatible changes, including end-user install/upgrade contract changes |
| **Minor** | Breaking Brain changes that preserve the core model, including renamed/removed core files, breaking vault-structure or folder-convention changes, or breaking tool/script/MCP contract changes |
| **Major** | Fundamental model changes to the artefact model, router contract, or agent bootstrap/entry flow |

## Changelog

The live changelog is tiered:

- `docs/CHANGELOG.md` — newest-first index
- `docs/changelog/vX.Y.Z.md` — one file per shipped version
- `docs/changelog/releases/vX.Y.Z-<slug>.md` — one file per shipped milestone release

For each shipped version:

- create `docs/changelog/vX.Y.Z.md` with a short top-line Summary (~60–75 chars, imperative, no period, no version suffix, specific identifier) plus supporting bullets
- write the same Summary text into the new row's `Summary` cell in `docs/CHANGELOG.md`. The Summary is one canonical text used three places — per-version top-line Summary, index `Summary` cell, release commit subject — drafted once before commit
- preserve `BREAKING —` in the `Summary` when the per-version entry requires install-manager action
- milestone rows use `Release: <title>`
- if the version closes a shipped `living/release`, add or update the matching file under `docs/changelog/releases/`

Never rewrite older per-version files to “fix history”. Add any correction in the current version's entry instead.

## Commit Messages

Every commit in this repo should have a scannable subject and a body that explains *why* the change exists, not just what the diff already shows. See [standards/commit-messages.md](standards/commit-messages.md) for the subject-line template, body structure, worked example, and drafting rules. For release commits, the subject is `<Summary> (vX.Y.Z)` where `<Summary>` is the canonical Summary text — the per-version file's top-line Summary, also filled into the matching `docs/CHANGELOG.md` index row — verbatim, parenthesised version suffix, never `as vX.Y.Z`. Release commits stay prefix-free. Non-versioned support commits must use exactly one of the prefixes `docs:`, `test:`, or `chore:`. Read `git diff` and `git diff --stat`, the matching index row, the corresponding `docs/changelog/vX.Y.Z.md` entry (if any), and recent `git log --oneline` output before drafting. Use only public-safe references in the message body — anything a stranger can verify using only `git log` and the public web.

## Testing

Run `make test` before committing. Uses `.venv` with Python 3.12.

```bash
make install   # first time — creates venv, installs dependencies
make test      # runs pytest
```

If you change artefact-library definitions or template-vault defaults, also run:

```bash
make sync-template-check         # verifies template-vault is truly in sync
make sync-template               # force-syncs _Config + .brain/tracking.json, then recompiles
```

`make test` now enforces the clean-state invariant through
`TestTemplateVaultSync::test_no_drift`, but `make sync-template-check` gives a
faster failure mode while you are iterating.

## AGENTS.md vs agents.local.md

`AGENTS.md` is checked in and universal — it applies to every contributor on every machine. Keep it lean: project identity, pre-commit canary, pointer to local overrides.

`agents.local.md` is gitignored and machine-specific. It holds:

- **Workspace paths** — vault locations, related repo paths
- **Workflow triggers that depend on the local environment** — e.g. post-core-commit canary (only relevant if a vault is co-located), deploy steps for a local staging environment
- **Machine-specific tool config** — local CLI paths, env vars, capability flags

**Rule of thumb:** if the instruction only makes sense when a specific path or local resource exists, it goes in `agents.local.md`. If it applies to anyone working on the repo, it goes in `AGENTS.md` or `docs/`.

## Contributor Skills

Contributor skills are Claude Code commands that help with brain-core development. They live in `.claude/commands/` — checked into git, so anyone who clones the repo gets them automatically.

To add a contributor skill, create `.claude/commands/{name}.md` with a `description` field in the frontmatter. Claude Code discovers commands from their description and makes them available as `/command-name`.

These are distinct from user skills (`_Config/Skills/` in vaults), which teach agents how to use vault tools. User skills ship via the template vault and are documented in `src/brain-core/plugins.md`; repo-level plugin authoring is documented in `docs/contributor/plugins.md`.

**Example `agents.local.md`:**

```markdown
# Local Overrides

## Vault Path

My vault: `/path/to/my/vault/`

## After Committing to Brain-Core

After committing brain-core changes in this repo, follow `.canaries/post-core-commit.local.md` to propagate and update vault documentation.
```

## Repository Structure

```
obsidian-brain/
├── src/
│   └── brain-core/              # source of truth for shipped `.brain-core/` files and tooling
│       ├── VERSION              # brain-core version
│       ├── index.md             # bootstrap entry point; routes to normal vs degraded paths
│       ├── session-core.md      # static authored source for canonical bootstrap content
│       ├── guide.md             # quick-start guide (ships into vaults)
│       ├── artefact-library/    # ready-to-install type definitions
│       ├── md-bootstrap.md      # degraded fallback bootstrap for non-MCP/no-session-mirror environments
│       ├── standards/extending/  # how to extend the vault: types, memories, triggers, principles
│       ├── triggers.md          # workflow trigger system
│       ├── colours.md           # folder colour system design
│       ├── plugins.md           # shipped plugin overview and vault-side plugin workflow
│       ├── scripts/             # brain-core tooling and vault-operation scripts
│       └── mcp/                 # MCP server and tool surface
├── src/scripts/                 # repo-maintenance helpers (e.g. template-vault sync)
├── tests/                       # test suite (make test)
├── docs/
│   ├── contributor/             # contributor-facing product docs and workflow guidance
│   │   ├── agents.md            # contributor workflow guidance for agents
│   │   ├── plugins.md           # writing and packaging plugin integrations
│   │   └── specification.md     # design rationale and structural decisions
│   ├── standards/               # shared contributor standards used across docs and workflow
│   ├── user/                    # user-facing docs (getting-started, workflows, system-guide, template-library, plugins, reference)
│   ├── functional/              # functional specs (mcp-tools, scripts, config)
│   ├── architecture/            # architectural docs (overview, bounded-contexts, documentation-philosophy, decisions/, security)
│   ├── CHANGELOG.md             # changelog index
│   ├── changelog/              # per-version and per-release changelog files
│   ├── CONTRIBUTING.md          # contributor guide and maintenance rules
│   └── README.md                # documentation router
├── template-vault/              # starter vault; copy to create a new Brain
└── README.md
```
