# Contributing to Obsidian Brain

Guide for anyone working on brain-core. For the pre-commit checklist, see [pre-commit canary](../.canaries/pre-commit.md).

**Agent contributors:** also read [contributing-agents.md](contributing-agents.md) and [standards/agent-workflow.md](standards/agent-workflow.md) for contributor-only workflow guidance.

## Canary Hook

A git pre-commit hook verifies the [pre-commit canary](canaries/pre-commit.md) was followed. It checks that `.canary--pre-commit` exists and covers all numbered items. The hook deletes the file after a successful commit so it can't go stale.

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

### User-facing docs (in `docs/`)

| File | Audience | Purpose |
|---|---|---|
| `user/getting-started.md` | Vault users | Installation, first vault, orientation |
| `user/workflows.md` | Vault users | Day-to-day usage patterns and examples |
| `user/system-guide.md` | Vault users | Artefact system mechanics, lifecycle, extension |
| `user/template-library-guide.md` | Vault users | Template library, available types, install procedures |
| `user-reference.md` | Vault users | Complete reference for every type, convention, config point |
| `specification.md` | Contributors | Design rationale, architecture, what ships in the vault |
| `tooling.md` | Contributors | Redirect → `functional/` and `architecture/decisions/` |
| `changelog.md` | Everyone | Version history |
| `standards/canary.md` | Contributors | How canaries work |
| `standards/agent-workflow.md` | Contributors | Workflow tiers for brain-core changes |

### Brain-core docs (in `src/brain-core/`, ship as `.brain-core/` in vaults)

| File | Audience | Purpose |
|---|---|---|
| `guide.md` | Vault users + agents | Quick-start guide, ships in the vault |
| `index.md` | Agents | Thin bootstrap entry point, read every session |
| `session-core.md` | Agents | Checked-in authored source for static core bootstrap content and core-doc references |
| `standards/extending/README.md` | Agents + contributors | How to extend the vault (add types, memories, principles) |
| `standards/` | Agents + contributors | Operational standards — naming, provenance, archiving, hub pattern, subfolders, user preferences |
| `colours.md` | Contributors | Colour system algorithm, CSS templates |
| `artefact-library/README.md` | Agents + contributors | Type catalogue, install steps, browsing guide |

Rule: if a document ships in `.brain-core/`, write it for normal vault agents.
Contributor-only process guidance belongs in `docs/`, even when it explains how
to change brain-core itself.

Link policy for shipped docs:

- Use relative markdown links for navigation between `.brain-core` source docs.
- Use plain code-form file paths for bootstrap instructions that tell an agent which file to read next.
- Keep Obsidian wikilinks only in examples of vault-native syntax that users or agents should actually write into artefacts. This includes artefact taxonomies, templates, provenance examples, transcript formats, and literal router/trigger snippets.

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

## Repository Structure

```
obsidian-brain/
├── src/
│   └── brain-core/              # core methodology (source of truth)
│       ├── VERSION              # brain-core version
│       ├── index.md             # bootstrap entry point; routes to normal vs degraded paths
│       ├── session-core.md      # static authored source for canonical bootstrap content
│       ├── guide.md             # quick-start guide (ships into vaults)
│       ├── artefact-library/    # ready-to-install type definitions
│       ├── md-bootstrap.md      # degraded fallback bootstrap for non-MCP/no-session-mirror environments
│       ├── standards/extending/  # how to add types, colours, triggers
│       ├── triggers.md          # workflow trigger system
│       ├── colours.md           # folder colour system design
│       ├── plugins.md           # plugin system
│       ├── scripts/             # tooling (compile_router, check, build_index, search)
│       └── mcp/                 # MCP server (brain_read, brain_search, brain_action)
├── tests/                       # test suite (make test)
├── docs/
│   ├── user/                    # user-facing docs (getting-started, workflows, system-guide, template-library)
│   ├── functional/              # functional specs (mcp-tools, scripts, config)
│   ├── architecture/            # architectural docs (overview, bounded-contexts, documentation-philosophy, decisions/, security)
│   ├── user-reference.md        # full reference for all types and conventions
│   ├── tooling.md               # redirect → functional/ and architecture/decisions/
│   ├── changelog.md             # version history
│   ├── plugins.md               # how to install and write plugins
│   └── specification.md         # design rationale and structural decisions
├── template-vault/              # starter vault; copy to create a new Brain
└── README.md
```
