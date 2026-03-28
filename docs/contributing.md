# Contributing to Obsidian Brain

Guide for anyone working on brain-core. For the pre-commit checklist, see [canaries/pre-commit.md](canaries/pre-commit.md).

**Agent contributors:** also read [contributing-agents.md](contributing-agents.md) for agent-specific workflow guidance.

## Canary Hook

A git pre-commit hook verifies the [pre-commit canary](canaries/pre-commit.md) was followed. It checks that `.canary--pre-commit` exists and covers all numbered items. The hook deletes the file after a successful commit so it can't go stale.

The hook source is tracked at `.githooks/pre-commit`. To activate:

```bash
make hooks      # sets git to use .githooks/ directory
```

Adding a new numbered item to the canary file automatically enforces it — no hook changes needed. See [standards/canary.md](standards/canary.md) for how canaries work generally.

## Documentation Layers

Brain has multiple documentation layers, each serving a different audience. Keeping them in sync is the single biggest maintenance challenge — most documentation bugs come from updating one layer but not others.

### User-facing docs (in `docs/`)

| File | Audience | Purpose |
|---|---|---|
| `user-guide.md` | Vault users | Example-driven walkthrough of day-to-day use |
| `user-reference.md` | Vault users | Complete reference for every type, convention, config point |
| `specification.md` | Contributors | Design rationale, architecture, what ships in the vault |
| `tooling.md` | Contributors | Technical design decisions (DD index) |
| `changelog.md` | Everyone | Version history |
| `standards/canary.md` | Contributors | How canaries work |

### Brain-core docs (in `src/brain-core/`, ship as `.brain-core/` in vaults)

| File | Audience | Purpose |
|---|---|---|
| `guide.md` | Vault users + agents | Quick-start guide, ships in the vault |
| `index.md` | Agents | System principles and always-rules, read every session |
| `extensions.md` | Agents + contributors | How to extend the vault (add types, memories, principles) |
| `standards/` | Agents + contributors | Operational standards — naming, provenance, archiving, hub pattern, subfolders, user preferences |
| `colours.md` | Contributors | Colour system algorithm, CSS templates |
| `artefact-library/README.md` | Agents + contributors | Type catalogue, install steps, browsing guide |

## Versioning

Bump `src/brain-core/VERSION` for any change to files under `src/brain-core/`, including doc-only edits. If it ships in `.brain-core/`, it gets a version bump — no exceptions. Follows [semver](https://semver.org/):

| Bump | When |
|---|---|
| **Patch** | Bug fixes, doc clarifications, additive changes (new types, new colours) |
| **Minor** | Breaking vault structure changes (renamed/removed core files, changed folder conventions) |
| **Major** | Fundamental model changes (artefact model, router contract, agent entry flow) |

## Changelog

New entry at the top of `docs/changelog.md`. Format: `## v{x.y.z} — YYYY-MM-DD`. Never edit past entries.

- **Bold lead** for significant changes — what changed and why in one sentence
- Regular bullets for smaller changes
- Include type counts when they change (e.g. "9 → 10 living types")
- Include colour hex values for new temporal types
- If a past entry is wrong, add a correction in the current version's entry

## Testing

Run `make test` before committing. Uses `.venv` with Python 3.12.

```bash
make install   # first time — creates venv, installs dependencies
make test      # runs pytest
```

## Agents.md vs agents.local.md

`Agents.md` is checked in and universal — it applies to every contributor on every machine. Keep it lean: project identity, pre-commit canary, pointer to local overrides.

`agents.local.md` is gitignored and machine-specific. It holds:

- **Workspace paths** — vault locations, related repo paths
- **Workflow triggers that depend on the local environment** — e.g. post-core-commit canary (only relevant if a vault is co-located), deploy steps for a local staging environment
- **Machine-specific tool config** — local CLI paths, env vars, capability flags

**Rule of thumb:** if the instruction only makes sense when a specific path or local resource exists, it goes in `agents.local.md`. If it applies to anyone working on the repo, it goes in `Agents.md` or `docs/`.

## Contributor Skills

Contributor skills are Claude Code commands that help with brain-core development. They live in `.claude/commands/` — checked into git, so anyone who clones the repo gets them automatically.

To add a contributor skill, create `.claude/commands/{name}.md` with a `description` field in the frontmatter. Claude Code discovers commands from their description and makes them available as `/command-name`.

These are distinct from user skills (`_Config/Skills/` in vaults), which teach agents how to use vault tools. User skills ship via the template vault and are documented in `src/brain-core/plugins.md`.

**Example `agents.local.md`:**

```markdown
# Local Overrides

## Vault Path

My vault: `/path/to/my/vault/`

## After Committing to Brain-Core

After committing brain-core changes in this repo, follow `.canaries/post-core-commit.local.md` to propagate and update vault documentation.
```

## Standards vs Docs

`docs/standards/` contains generic, reusable patterns — things that could be adopted by any project without modification. The rest of `docs/` contains brain-specific conventions, guides, and references. When a pattern emerges from brain-specific work but has no inherent dependency on brain, extract the generic version into `docs/standards/` and keep project-specific usage in `docs/`.