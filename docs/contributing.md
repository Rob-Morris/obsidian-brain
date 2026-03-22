# Contributing to Obsidian Brain

Guide for anyone (human or agent) working on brain-core. For the pre-commit checklist, see [canaries/pre-commit.md](canaries/pre-commit.md).

## Canary Hook

A git pre-commit hook verifies the [pre-commit canary](canaries/pre-commit.md) was followed. It checks that `.canary--pre-commit` exists and covers all numbered items. The hook deletes the file after a successful commit so it can't go stale.

The hook source is tracked at `.githooks/pre-commit`. To activate:

```bash
make hooks      # sets git to use .githooks/ directory
```

Adding a new numbered item to the canary file automatically enforces it — no hook changes needed. See [canary.md](canary.md) for how canaries work generally.

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
| `canary.md` | Contributors | How canaries work |

### Brain-core docs (in `src/brain-core/`, ship as `.brain-core/` in vaults)

| File | Audience | Purpose |
|---|---|---|
| `guide.md` | Vault users + agents | Quick-start guide, ships in the vault |
| `index.md` | Agents | System principles and always-rules, read every session |
| `extensions.md` | Agents + contributors | How to extend the vault (add types, patterns, preferences) |
| `library.md` | Agents + contributors | How to browse and install artefact types |
| `colours.md` | Contributors | Colour system algorithm, CSS templates |
| `artefact-library/README.md` | Agents + contributors | Type catalogue, colour table, install steps |

### Why drift happens

The same fact often appears in multiple files. For example, "Plans lifecycle is `draft` → `approved` → `implementing` → `completed`" appears in the Plans taxonomy, `docs/user-reference.md`, `src/brain-core/guide.md`, and `src/brain-core/artefact-library/README.md`. When a commit updates some but not all, the docs drift.

## Versioning

Bump `src/brain-core/VERSION` for any behaviour change. Follows [semver](https://semver.org/):

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

## Multi-Repo Workflow

Brain-core is developed here (`src/brain-core/`) and deployed to vaults by copying the whole directory to `.brain-core/`. When changes span brain-core and a vault:

1. Implement and commit core changes in this repo first
2. Copy `src/brain-core/` to the vault's `.brain-core/`
3. Follow [canaries/post-propagation.md](canaries/post-propagation.md) — update vault log, daily note, and master design doc
4. Commit in the vault repo

Never deploy to both simultaneously. Core-first, always.

Step 3 depends on having a local vault — the post-propagation canary reference belongs in `agents.local.md`, not `Agents.md`. See "Agents.md vs agents.local.md" below.

## Agents.md vs agents.local.md

`Agents.md` is checked in and universal — it applies to every contributor on every machine. Keep it lean: project identity, pre-commit canary, pointer to local overrides.

`agents.local.md` is gitignored and machine-specific. It holds:

- **Workspace paths** — vault locations, related repo paths
- **Workflow triggers that depend on the local environment** — e.g. post-propagation canary (only relevant if a vault is co-located), deploy steps for a local staging environment
- **Machine-specific tool config** — local CLI paths, env vars, capability flags

**Rule of thumb:** if the instruction only makes sense when a specific path or local resource exists, it goes in `agents.local.md`. If it applies to anyone working on the repo, it goes in `Agents.md` or `docs/`.

**Example `agents.local.md`:**

```markdown
# Local Overrides — Rob's Machine

## Vault Path

Rob's vault: `/Users/robmorris/Library/Mobile Documents/iCloud~md~obsidian/Documents/Brain/`

## After Propagating to Vault

When copying `src/brain-core/` to the vault's `.brain-core/`, follow `docs/canaries/post-propagation.md` before committing in the vault repo.
```

## Standards vs Docs

`standards/` contains generic, reusable patterns — things that could be adopted by any project without modification. `docs/` contains brain-specific conventions, guides, and references. When a pattern emerges from brain-specific work but has no inherent dependency on brain, extract the generic version into `standards/` and have `docs/` reference it.

## Common Pitfalls

### Stale install procedures

Install/extension procedures appear in four places (`user-reference.md`, `extensions.md`, `library.md`, `artefact-library/README.md`). Since v0.9.12, colours are auto-generated — any mention of manual CSS steps or colour picking is stale.

### Type table vs defaults

The quick-start guide (`src/brain-core/guide.md`) type table should show template vault defaults. The artefact library README shows all available types. These are different lists — don't copy one into the other.
