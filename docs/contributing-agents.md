# Contributing — Agent Instructions

Agent-specific guidance for working on brain-core. Read [contributing.md](contributing.md) first for general rules (versioning, changelog, testing, doc layers).

## Why Drift Happens

The same fact often appears in multiple files. For example, "Plans lifecycle is `draft` → `approved` → `implementing` → `completed`" appears in the Plans taxonomy, `docs/user-reference.md`, `src/brain-core/guide.md`, and `src/brain-core/artefact-library/README.md`. When a commit updates some but not all, the docs drift.

The pre-commit canary's cross-check tasks exist specifically to catch this. Follow them carefully — grep for the values you changed and verify every occurrence.

## Multi-Repo Workflow

Brain-core is developed here (`src/brain-core/`) and deployed to vaults by copying the whole directory to `.brain-core/`. When changes span brain-core and a vault:

1. Implement and commit core changes in this repo first
2. Copy `src/brain-core/` to the vault's `.brain-core/`
3. If a post-core-commit canary exists locally (`.canaries/post-core-commit.local.md`), follow it — it handles vault-specific propagation steps like updating logs and documentation
4. Commit in the vault repo

Never deploy to both simultaneously. Core-first, always.

Step 3 depends on having a local vault. Post-core-commit canaries are machine-specific and belong in `agents.local.md`, not `Agents.md`.

## Common Pitfalls

### Stale install procedures

Install/extension procedures appear in three places (`user-reference.md`, `extensions.md`, `artefact-library/README.md`). Since v0.9.12, colours are auto-generated — any mention of manual CSS steps or colour picking is stale.

### Type table vs defaults

The quick-start guide (`src/brain-core/guide.md`) type table should show template vault defaults. The artefact library README shows all available types. These are different lists — don't copy one into the other.

### When to update guide.md

The quick-start guide (`src/brain-core/guide.md`) ships in every vault and should be updated when:
- New artefact types are added to the template vault defaults
- Core conventions change (naming, frontmatter, filing)
- New user-facing tooling is introduced
- Workflows are added or modified
