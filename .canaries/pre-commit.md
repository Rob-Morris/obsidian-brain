# Canary: Pre-Commit

Follow before every commit.

## Tasks

[1] **Tests pass.** Run `make test`. All tests must pass.

[2] **Version bumped.** Bump `src/brain-core/VERSION` for any change to files under `src/brain-core/`, including doc-only edits. If it ships in `.brain-core/`, it gets a version bump. Patch = doc clarifications, additive changes; minor = breaking vault structure; major = fundamental model changes.

[3] **Changelog updated.** New entry at the top of `docs/changelog.md`. Format: `## v{x.y.z} — YYYY-MM-DD`. Bold lead for significant changes. Never edit past entries.

[4] **Docs updated.** Update every file affected by your change:

    [4a] **Artefact type** — if added/removed/renamed: `src/brain-core/artefact-library/README.md` (source of truth), `docs/user/template-library-guide.md` (if guide needs updating), `docs/specification.md` (starter vault list if default)
    [4b] **Template vault defaults** — if added/removed: `docs/user/getting-started.md` (What's in the Vault), `src/brain-core/guide.md` (type table), `docs/specification.md` (starter vault section)
    [4c] **Status/lifecycle values** — `src/brain-core/artefact-library/README.md` (conventions section), `docs/user/system-guide.md` (if system-level lifecycle affected)
    [4d] **Install/extension procedures** — `docs/user/system-guide.md` (Extension), `src/brain-core/standards/extending/README.md`, `src/brain-core/artefact-library/README.md` (Installing a type)
    [4e] **Colour system** — `src/brain-core/colours.md`, `docs/user-reference.md` (Colour System), `docs/specification.md` (Colour System)
    [4f] **System design/architecture** — `docs/architecture/overview.md`, `docs/architecture/decisions/` (if new design decision — create the DD file AND add a row to `docs/architecture/decisions/README.md`), `src/brain-core/index.md` (if principles change)
    [4g] **Day-to-day workflows** — `docs/user/workflows.md`, `src/brain-core/guide.md`
    [4h] **Tooling** — scripts, MCP tools, config: `docs/functional/scripts.md` (scripts), `docs/functional/mcp-tools.md` (MCP tools), `docs/functional/config.md` (config), `src/brain-core/scripts/README.md` (co-located module map), `docs/user-reference.md` (Tooling summary)
    [4i] **General pattern** — hub, provenance, archiving: `src/brain-core/standards/`, `docs/specification.md`, `docs/user/system-guide.md`
    [4j] **Template vault** — if artefact types, taxonomy, config, or default structure changed: update `template-vault/` to match
    [4k] **Artefact library metadata** — if taxonomy, README, template, or SKILL changed in `artefact-library/`: update `manifest.yaml` and `schema.yaml` in the same type directory to match
    [4l] **Security model** — if path boundaries, write guards, privilege model, or safe write pattern changed: `docs/architecture/security.md`

[5] **Shared facts cross-checked.** Grep for the specific values you changed to catch stale references in other files. With the three-layer structure, most facts now live in one place — only check if you changed a shared fact:

    [5a] **Type counts** — count `artefact-library/{living,temporal}/` directories as canonical source, then verify prose counts match in `src/brain-core/artefact-library/README.md` and `docs/specification.md`. Do not trust existing prose numbers — always recount from directories.
    [5b] **Template vault defaults list** — `docs/user/getting-started.md`, `src/brain-core/guide.md`, `docs/specification.md`
    [5c] **Status values** — `src/brain-core/artefact-library/README.md`, `docs/user/system-guide.md`
    [5d] **Install step counts** — `docs/user/system-guide.md`, `src/brain-core/artefact-library/README.md`, `src/brain-core/standards/extending/`

## Log

After following the tasks above, ALWAYS write a log file named `.canary--pre-commit` to the repo root.
Every task must have exactly one log line with a matching ID. Optionally indent sub-items for readability.

Log format: `[id] Short name: status, comment`

The `[id]` brackets are literal. Comment is optional for `done`, required for `skip`.

### done

`done` means you performed the action. Only write `done` if you actually did the thing. You may optionally add detail after `done`, e.g. `done, updated 4 files` or `done, 353 passed`.

### skip

`skip, {reason}` means you did not perform the action. The reason must be your own assessment of why the action wasn't needed — describe what you evaluated and what you concluded. Do not copy example reasons from this brief; write what actually applies to your situation.

Note: `skip` uses a comma separator (not colon) to avoid ambiguity with the label separator.

### Example

```
[1] Tests pass: done
[2] Version bumped: done
[3] Changelog updated: done
[4] Docs updated: done
    [4a] Artefact type: skip, no type changes
    [4b] Template vault defaults: skip, no changes
    [4c] Status/lifecycle values: skip, no changes
    [4d] Install/extension procedures: skip, no changes
    [4e] Colour system: skip, no changes
    [4f] System design/architecture: skip, no changes
    [4g] Day-to-day workflows: skip, no changes
    [4h] Tooling: skip, no changes
    [4i] General pattern: skip, no changes
    [4j] Template vault: skip, no structure changes
    [4k] Artefact library metadata: skip, no artefact-library changes
    [4l] Security model: skip, no security changes
[5] Shared facts cross-checked: done
    [5a] Type counts: skip, no changes
    [5b] Template vault defaults list: skip, no changes
    [5c] Status values: skip, no changes
    [5d] Install step counts: skip, no changes
```

The pre-commit hook checks this file exists and covers all tasks.
