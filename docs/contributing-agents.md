# Contributing — Agent Instructions

Instructions for agents contributing to brain-core. Read [contributing.md](contributing.md) first for general rules (versioning, changelog, testing, doc layers).

Contributor standards:

- [Agent Workflow](standards/agent-workflow.md) — contributor workflow tiers and escalation bar
- [Canary](standards/canary.md) — subjective-work checklists and log enforcement

## Bootstrap Contract

Bootstrap changes have an unusually high drift risk because the same user-facing behaviour spans JSON, markdown, install text, and fallback docs.

When touching bootstrap:

- Treat `session.py` as the canonical bootstrap owner. Do not add payload content independently to `brain_session`, `index.md`, or fallback docs.
- Preserve parity between `brain_session` JSON and `.brain/local/session.md` for shared bootstrap content.
- Keep `index.md` thin. It is a bootloader, not a second payload surface.
- Treat `md-bootstrap.md` as the degraded fallback only, not as a peer of the canonical session model.
- Do not put repo contributor workflow policy into shipped bootstrap surfaces. If it ships in `.brain-core/`, write it for normal vault agents, not contributors to `obsidian-brain`.

## Documentation Link Policy

When editing docs that ship in `.brain-core/`:

- Use relative markdown links for doc-to-doc navigation inside brain-core source docs.
- Use plain code-form file paths for imperative bootstrap instructions like “read this file next”.
- Keep Obsidian wikilinks only for vault-native syntax examples that agents or users should actually write into artefacts. This includes artefact taxonomies, templates, provenance examples, transcript formats, and literal router/trigger snippets.
- Do not hardcode `.brain-core/...` in source-doc navigation unless the document is explicitly describing the installed vault layout.

## Documentation Entry Point

[docs/README.md](README.md) is the documentation entry point. It routes to all three layers:

- **User layer** (`user/`) — how to use the system: `user/getting-started.md`, `user/system-guide.md`, `user/template-library-guide.md`, `user/workflows.md`
- **Functional layer** (`functional/`) — what it does: `functional/mcp-tools.md`, `functional/scripts.md`, `functional/config.md`
- **Architectural layer** (`architecture/`) — how and why it's built this way: `architecture/overview.md`, `architecture/documentation-philosophy.md`, `architecture/decisions/`

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

## Installing for Users

When a user asks an agent working in this repo to install a Brain vault on their behalf, separate the job into two outcomes:

1. Vault scaffold created at the requested path
2. MCP setup completed (`.venv` + dependency install + registration)

Do not treat those as all-or-nothing unless the user explicitly requires MCP to be ready immediately.

Preferred command selection:

- Use `bash install.sh --force --skip-mcp <path>` in restricted, sandboxed, or otherwise uncertain environments.
- Use `bash install.sh --force <path>` only when package index access is expected to work.
- If the user explicitly wants a vault only, use `--skip-mcp` even when network access is available.

Reporting expectations:

- State clearly whether the vault scaffold succeeded.
- State separately whether MCP setup succeeded.
- If MCP setup fails, do not frame the whole install as failed when the vault is already usable as a markdown vault.
- Surface the installer's retry commands so the user or a later agent can finish MCP setup without re-scaffolding the vault.

Why this matters:

- `install.sh` can complete the vault scaffold before hitting network-dependent dependency installation.
- Agent sandboxes commonly block package index access even when local file operations succeed.
- Repo guidance belongs here and in `AGENTS.md`, not in shipped `.brain-core/` bootstrap files.

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
