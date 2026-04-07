# Contributing — Agent Instructions

Agent-specific guidance for working on brain-core. Read [contributing.md](contributing.md) first for general rules (versioning, changelog, testing, doc layers).

## Documentation Entry Point

[docs/README.md](README.md) is the documentation entry point. It routes to all three layers:

- **User layer** (`user/`) — how to use the system: `user/getting-started.md`, `user/system-guide.md`, `user/template-library-guide.md`, `user/workflows.md`
- **Functional layer** (`functional/`) — what it does: `functional/mcp-tools.md`, `functional/scripts.md`, `functional/config.md`
- **Architectural layer** (`architecture/`) — how and why it's built this way: `architecture/overview.md`, `architecture/decisions/`

## When to Update Which Layer

| Change type | Update |
|---|---|
| New MCP tool or change to tool behaviour | `functional/mcp-tools.md` |
| New script or change to script arguments | `functional/scripts.md` |
| Config, profiles, or merge rules | `functional/config.md` |
| New artefact type, lifecycle, or frontmatter convention | `user/system-guide.md` |
| Template vault defaults change | `user/template-library-guide.md` |
| Install, upgrade, or first-vault steps | `user/getting-started.md` |
| Day-to-day workflow or MCP tool workflow | `user/workflows.md` |
| Architectural decision (non-obvious, cross-cutting) | Add a DD file to `architecture/decisions/` |
| System boundary or security model | `architecture/overview.md` or `architecture/security.md` (if present) |

When in doubt, check `docs/README.md` — if a doc file is listed there, it's a canonical reference that may need updating.

## Why Drift Happens

The same fact often appears in multiple files. For example, "Plans lifecycle is `draft` → `approved` → `implementing` → `completed`" appears in the Plans taxonomy, `docs/user/system-guide.md`, `src/brain-core/guide.md`, and `src/brain-core/artefact-library/README.md`. When a commit updates some but not all, the docs drift.

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

Install/extension procedures appear in `user/getting-started.md`, `standards/extending/README.md`, and `artefact-library/README.md`. Since v0.9.12, colours are auto-generated — any mention of manual CSS steps or colour picking is stale.

### Type table vs defaults

The quick-start guide (`src/brain-core/guide.md`) type table should show template vault defaults. The artefact library README shows all available types. These are different lists — don't copy one into the other.

### When to update guide.md

The quick-start guide (`src/brain-core/guide.md`) ships in every vault and should be updated when:
- New artefact types are added to the template vault defaults
- Core conventions change (naming, frontmatter, filing)
- New user-facing tooling is introduced
- Workflows are added or modified
