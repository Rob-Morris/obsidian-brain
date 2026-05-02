# Canary: Pre-Commit

Follow before every commit.

## Tasks

[1] **Tests pass.** Run `make test`. All tests must pass.

[2] **Version bumped.** Bump `src/brain-core/VERSION` for any change to files under `src/brain-core/`, including doc-only edits. If it ships in `.brain-core/`, it gets a version bump. Also bump it for end-user install or upgrade contract changes (`install.sh`, installer docs, upgrade entry-point guidance) even when those files live outside `src/brain-core/`. Use the repo's pre-1.0 semver policy: patch = bug fixes, doc clarifications, additive, backward-compatible changes, including backward-compatible install/upgrade contract changes; minor = breaking Brain changes that preserve the core model, including vault-structure or tool/script/MCP contract changes; major = fundamental model changes to the artefact model, router contract, or agent bootstrap/entry flow.

[3] **Changelog updated.** Follow `docs/standards/changelog.md`. Create `docs/changelog/vX.Y.Z.md` with a short top-line Summary (~60–75 chars, imperative, no period, no version suffix, specific identifier) plus supporting bullets, and write the same Summary text into the matching row's `Summary` cell in `docs/CHANGELOG.md`. The Summary is one canonical text used three places — per-version top-line Summary, index `Summary` cell, release commit subject (with a `(vX.Y.Z)` suffix) — drafted once before commit. Preserve a `BREAKING —` prefix when install-manager action is required. Milestone rows use `Release: <title>`. Add or update `docs/changelog/releases/vX.Y.Z-<slug>.md` when the version closes a shipped release. Never rewrite older per-version files.

[4] **Docs updated.** Update every file affected by your change:

    [4a] **Artefact type** — if added/removed/renamed: `src/brain-core/artefact-library/README.md` (source of truth), `docs/user/template-library-guide.md` (if guide needs updating), `docs/contributor/specification.md` (starter vault list if default)
    [4b] **Template vault defaults** — if added/removed: `docs/user/getting-started.md` (What's in the Vault), `src/brain-core/guide.md` (type table), `docs/contributor/specification.md` (starter vault section)
    [4c] **Status/lifecycle values** — `src/brain-core/artefact-library/README.md` (conventions section), `docs/user/system-guide.md` (if system-level lifecycle affected)
    [4d] **Install/extension procedures** — `docs/user/system-guide.md` (Extension), `src/brain-core/standards/extending/README.md`, `src/brain-core/artefact-library/README.md` (Installing a type)
    [4e] **Colour system** — `src/brain-core/colours.md`, `docs/user/user-reference.md` (Colour System), `docs/contributor/specification.md` (Colour System)
    [4f] **System design/architecture / bootstrap principles** — `docs/architecture/overview.md`, `docs/architecture/decisions/` (if new design decision — create the DD file AND add a row to `docs/architecture/decisions/README.md`), `src/brain-core/session-core.md` (if bootstrap principles or curated core-doc/standards links change), `src/brain-core/index.md` / `src/brain-core/md-bootstrap.md` (if bootstrap entry flow changes)
    [4g] **Day-to-day workflows** — `docs/user/workflows.md`, `src/brain-core/guide.md`
    [4h] **Tooling** — scripts, MCP tools, config: `docs/functional/scripts.md` (scripts), `docs/functional/mcp-tools.md` (MCP tools), `docs/functional/config.md` (config), `src/brain-core/scripts/README.md` (co-located module map), `docs/user/user-reference.md` (Tooling summary)
    [4i] **General pattern** — hub, provenance, archiving: `src/brain-core/standards/`, `docs/contributor/specification.md`, `docs/user/system-guide.md`
    [4j] **Template vault** — if artefact types, taxonomy, config, or default structure changed: update `template-vault/` to match
    [4k] **Artefact library metadata** — if taxonomy, README, template, or SKILL changed in `artefact-library/`: update `manifest.yaml` and `schema.yaml` in the same type directory to match
    [4l] **Security model** — if path boundaries, write guards, privilege model, or safe write pattern changed: `docs/architecture/security.md`
    [4m] **Plugin system** — if plugin conventions, install procedures, or plugin API changed: `docs/user/plugins.md`, `docs/contributor/plugins.md`, `src/brain-core/plugins.md`
    [4n] **Doc structure** — if doc files added/moved/removed or navigation changed: `docs/README.md` (router), the relevant layer `README.md` files such as `docs/contributor/README.md` or `docs/standards/README.md`, and `docs/CONTRIBUTING.md` (Documentation Layers section)
    [4o] **Contribution process** — if canary items, pre-commit workflow, versioning rules, testing procedures, changelog rules, or commit-message rules changed: `docs/CONTRIBUTING.md`, `docs/contributor/README.md`, `docs/contributor/agents.md`, `docs/standards/canary.md` (canary system standard), `docs/standards/changelog.md` (changelog standard), `docs/standards/commit-messages.md` (commit message standard)
    [4p] **Bootstrap audience boundary** — if bootstrap surfaces or contributor-doc boundaries changed: `docs/architecture/documentation-philosophy.md`, `docs/architecture/decisions/dd-038-unified-session-bootstrap.md`, `docs/CONTRIBUTING.md`; shipped `.brain-core/` bootstrap docs must stay written for normal vault agents, not repo contributors

[5] **Shared facts cross-checked.** Grep for the specific values you changed to catch stale references in other files. With the three-layer structure, most facts now live in one place — only check if you changed a shared fact:

    [5a] **Type counts** — count `artefact-library/{living,temporal}/` directories as canonical source, then verify prose counts match in `src/brain-core/artefact-library/README.md` and `docs/contributor/specification.md`. Do not trust existing prose numbers — always recount from directories.
    [5b] **Template vault defaults list** — `docs/user/getting-started.md`, `src/brain-core/guide.md`, `docs/contributor/specification.md`
    [5c] **Status values** — `src/brain-core/artefact-library/README.md`, `docs/user/system-guide.md`
    [5d] **Install step counts** — `docs/user/system-guide.md`, `src/brain-core/artefact-library/README.md`, `src/brain-core/standards/extending/`
    [5e] **Bootstrap audience split** — if changing shipped bootstrap docs or contributor workflow guidance, grep the relevant surfaces and confirm contributor-only process language stays out of `.brain-core/` bootstrap docs

[6] **Commit message drafted per standard.** See `docs/standards/commit-messages.md`. Read `git diff` and `git diff --stat`, the matching `docs/CHANGELOG.md` index row, the corresponding `docs/changelog/vX.Y.Z.md` entry (if any), and `git log --oneline -15` before writing. For release commits, the subject is `<Summary> (vX.Y.Z)` where `<Summary>` is the canonical Summary text drafted in [3] (per-version top-line Summary = index `Summary` cell) — verbatim, parenthesised version suffix, never `as vX.Y.Z`. Body paraphrases the per-version file's supporting prose, explains *why* (not just what), and includes a public-safe reference (anything a stranger can verify from `git log` or the public web) when one exists.

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
    [4m] Plugin system: skip, no plugin changes
    [4n] Doc structure: skip, no doc files added/moved/removed
    [4o] Contribution process: skip, no process changes
    [4p] Bootstrap audience boundary: skip, bootstrap audience unchanged
[5] Shared facts cross-checked: done
    [5a] Type counts: skip, no changes
    [5b] Template vault defaults list: skip, no changes
    [5c] Status values: skip, no changes
    [5d] Install step counts: skip, no changes
    [5e] Bootstrap audience split: skip, no bootstrap or contributor-process changes
[6] Commit message drafted per standard: done
```

The pre-commit hook checks this file exists and covers all tasks.
