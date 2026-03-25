# Canary: Pre-Commit

Follow before every commit.

## Tasks

[1] **Tests pass.** Run `make test`. All tests must pass.

[2] **Version bumped.** Bump `src/brain-core/VERSION` for any change to files under `src/brain-core/`, including doc-only edits. If it ships in `.brain-core/`, it gets a version bump. Patch = doc clarifications, additive changes; minor = breaking vault structure; major = fundamental model changes.

[3] **Changelog updated.** New entry at the top of `docs/changelog.md`. Format: `## v{x.y.z} — YYYY-MM-DD`. Bold lead for significant changes. Never edit past entries.

[4] **Docs updated.** Update every file affected by your change:

    [4a] **Artefact type** — if added/removed/renamed: `src/brain-core/artefact-library/README.md` (type table), `docs/user-reference.md` (type spec), `docs/specification.md` (starter vault list if default), `docs/user-guide.md` (vault overview)
    [4b] **Template vault defaults** — if added/removed: `docs/user-guide.md` (starter set list), `src/brain-core/guide.md` (type table), `docs/specification.md` (starter vault section)
    [4c] **Status/lifecycle values** — `docs/user-reference.md` (type spec + status table), `src/brain-core/artefact-library/README.md` (conventions section), `src/brain-core/guide.md` (status section)
    [4d] **Install/extension procedures** — `docs/user-reference.md` (Extending Your Vault), `src/brain-core/extensions.md`, `src/brain-core/artefact-library/README.md` (Installing a type)
    [4e] **Colour system** — `src/brain-core/colours.md`, `docs/user-reference.md` (Colour System), `docs/specification.md` (Colour System)
    [4f] **System design/architecture** — `docs/specification.md`, `src/brain-core/index.md` (if principles change), `src/brain-core/extensions.md` (if patterns change)
    [4g] **Day-to-day workflows** — `docs/user-guide.md`, `src/brain-core/guide.md`, `docs/user-reference.md` (Workflows)
    [4h] **Tooling** — scripts, MCP tools: `docs/tooling.md`, `docs/user-reference.md` (Tooling), `src/brain-core/guide.md` (Tooling)
    [4i] **General pattern** — hub, provenance, archiving: `src/brain-core/standards/`, `docs/specification.md`, `docs/user-reference.md` (Workflows)

[5] **Shared facts cross-checked.** Grep for the specific values you changed to catch stale references in other files:

    [5a] **Type counts** — living/temporal counts in library README, changelog, specification
    [5b] **Template vault defaults list** — user-guide, quick-start guide, specification
    [5c] **Status values** — user-reference, library README, quick-start guide
    [5d] **Install step counts** — user-reference, library README, extensions.md

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
[5] Shared facts cross-checked: done
    [5a] Type counts: skip, no changes
    [5b] Template vault defaults list: skip, no changes
    [5c] Status values: skip, no changes
    [5d] Install step counts: skip, no changes
```

The pre-commit hook checks this file exists and covers all tasks.
