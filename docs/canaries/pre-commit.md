# Pre-Commit Canary

Follow before every commit. See [canary.md](../canary.md) for how canaries work.

## Items

1. **Tests pass.** Run `make test`. All tests must pass.

2. **Version bumped.** Bump `src/brain-core/VERSION` for any behaviour change. Patch = additive changes, minor = breaking vault structure, major = fundamental model changes.

3. **Changelog updated.** New entry at the top of `docs/changelog.md`. Format: `## v{x.y.z} — YYYY-MM-DD`. Bold lead for significant changes. Never edit past entries.

4. **Docs updated.** Consult the routing table below and update every file affected by your change.

    | If you changed... | Update these files |
    |---|---|
    | **Anything** | `docs/changelog.md`, `src/brain-core/VERSION` |
    | **Artefact type** (added/removed/renamed) | `src/brain-core/artefact-library/README.md` (type table, colour table), `docs/user-reference.md` (type spec), `docs/specification.md` (starter vault list if default), `docs/user-guide.md` (vault overview) |
    | **Template vault defaults** (added/removed) | `docs/user-guide.md` (starter set list), `src/brain-core/guide.md` (type table), `docs/specification.md` (starter vault section) |
    | **Status/lifecycle values** | `docs/user-reference.md` (type spec + status table), `src/brain-core/artefact-library/README.md` (conventions section), `src/brain-core/guide.md` (status section) |
    | **Install/extension procedures** | `docs/user-reference.md` (Extending Your Vault), `src/brain-core/extensions.md`, `src/brain-core/library.md`, `src/brain-core/artefact-library/README.md` (Installing a type) |
    | **Colour system** | `src/brain-core/colours.md`, `docs/user-reference.md` (Colour System), `docs/specification.md` (Colour System) |
    | **System design/architecture** | `docs/specification.md`, `src/brain-core/index.md` (if principles change), `src/brain-core/extensions.md` (if patterns change) |
    | **Day-to-day workflows** | `docs/user-guide.md`, `src/brain-core/guide.md`, `docs/user-reference.md` (Workflows) |
    | **Tooling** (scripts, MCP tools) | `docs/tooling.md`, `docs/user-reference.md` (Tooling), `src/brain-core/guide.md` (Tooling) |
    | **General pattern** (hub, provenance, archiving) | `src/brain-core/extensions.md`, `docs/specification.md`, `docs/user-reference.md` (Workflows) |

5. **Shared facts cross-checked.** Grep for the specific values you changed to catch stale references in other files:

    - **Type counts** (living/temporal) — library README, changelog, specification
    - **Template vault defaults list** — user-guide, quick-start guide, specification
    - **Status values** — user-reference, library README, quick-start guide
    - **Install step counts and procedures** — user-reference, library README, library.md, extensions.md
    - **Colour table entries** — library README (one row per temporal type)

## Log

After following the items above, write `.canary--pre-commit` at the repo root:

```
[1] done
[2] done
[3] done
[4] done
[5] skip: no docs impact
```

The pre-commit hook checks this file exists and covers all items.
