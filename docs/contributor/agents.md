# Contributing — Agent Instructions

Instructions for agents contributing to brain-core. Read [CONTRIBUTING.md](../CONTRIBUTING.md) first for general rules (versioning, changelog, testing, doc layers).

Contributor standards:

- [Agent Workflow](../standards/agent-workflow.md) — contributor workflow tiers and escalation bar
- [Canary](../standards/canary.md) — subjective-work checklists and log enforcement
- [Changelog](../standards/changelog.md) — tiered public release-history standard
- [Commit Messages](../standards/commit-messages.md) — release Summary subjects, required non-versioned prefixes, body structure, and drafting process

## Agent Authorisation Contract

Every ordinary command declares its initial authorisation class and domain-owned
preparation/admission strategy. Normal content is initially authorised within
credential permissions; exceptional consent is scoped to the selected Brain and
active owner. Keep permission changes separate from explicit `access.request`,
and never add adapter auto-request/retry or elapsed-time renewal. Bootstrap,
static above-maximum discovery and paged controls derive from these typed
contracts; measure actual UTF-8 envelopes when changing their byte budgets.

## Bootstrap Contract

Bootstrap changes have an unusually high drift risk because the same user-facing behaviour spans JSON, markdown, install text, and fallback docs.

When touching bootstrap:

- Treat `session.start` as the canonical bootstrap owner. Do not add payload content independently to MCP, `index.md`, or fallback docs.
- Treat `session-core.md` as the authored source for bootstrap principles and curated core-doc references. When a core doc, standard, or bootstrap principle changes, decide whether `session-core.md` must change too. The current `session.py` implementation parses `## Core Docs` and `## Standards` as required H2 sections (one of each) and `tests/test_session_core.py` guards that shape — this is implementation hygiene for the eager-load path, not a permanent bootstrap contract; expect it to relax when bootstrap moves to lazy-loading.
- Treat `scripts/_bootstrap/` as the owner for launcher-safe shared bootstrap leaves. Runtime handoff belongs in `runtime.py`; launcher-safe diagnostics belong in `diagnostics.py`; env-aware vault discovery belongs in `vaults.py`; Brain-local ignore-rule scaffold belongs in `workspace_scaffold.py`; shared MCP/config-layout helpers belong in `mcp_state.py`. Canonical transport intent/projection planning belongs in `mcp_registration.py`, registered worksets in `mcp_inventory.py`, bounded legacy admission in `mcp_migration.py`, and direct-script delegation in `mcp_transport.py`. Ownership-safe client discovery installation belongs in `agent_skills.py`, backed by checked-in templates under `client-adapters/`, not wrapper entry points.
- Preserve parity between `session.start` JSON and `.brain/local/session.md` for shared bootstrap content.
- Managed host approvals use canonical snapshots exported by `approval_contract.py`, pure `_bootstrap/approval_policy.py`, native adapters and exact ownership. Keep orchestration in `cli/_launcher/approval_management.py` and `approval_lifecycle.py`; reuse registration locks and `FilePlan`. Do not fold approval policy into transport ownership or synced vault configuration.
- Keep `index.md` thin. It is a bootloader, not a second payload surface.
- Keep the bootstrap routes explicit: MCP `session.start`, then the `brain session start --json` launcher alternative, then supported direct scripts with their dependency requirements. The generated `.brain/local/session.md` is a projection of the canonical bootstrap. `md-bootstrap.md` is the authored Markdown fallback for agents without usable MCP, CLI, scripts or generated assets; it must work without code execution or compilation.
- Do not put repo contributor workflow policy into shipped bootstrap surfaces. If it ships in `.brain-core/`, write it for normal vault agents, not contributors to `obsidian-brain`.

## Documentation Link Policy

When editing docs that ship in `.brain-core/`:

- Use relative markdown links for doc-to-doc navigation inside brain-core source docs.
- Use plain code-form file paths for imperative bootstrap instructions like “read this file next”.
- Keep Obsidian wikilinks only for vault-native syntax examples that agents or users should actually write into artefacts. This includes artefact taxonomies, templates, provenance examples, transcript formats, and literal router/trigger snippets.
- Do not hardcode `.brain-core/...` in source-doc navigation unless the document is explicitly describing the installed vault layout.

## Documentation Entry Point

[docs/README.md](../README.md) is the documentation entry point. It routes to the main doc layers and contributor docs:

- **User layer** (`user/`) — how to use the system: `user/getting-started.md`, `user/system-guide.md`, `user/template-library-guide.md`, `user/plugins.md`, `user/workflows.md`, `user/user-reference.md`
- **Functional layer** (`functional/`) — what it does: `functional/mcp-tools.md`, `functional/scripts.md`, `functional/cli.md`, `functional/config.md`
- **Architectural layer** (`architecture/`) — how and why it's built this way: `architecture/overview.md`, `architecture/bounded-contexts.md`, `architecture/security.md`, `architecture/documentation-philosophy.md`, `architecture/decisions/`
- **Contributor docs** (`contributor/`) — repo-facing product and workflow surfaces: `contributor/specification.md`, `contributor/agents.md`, `contributor/plugins.md`

## Versioned helper surfaces

When a change touches the machine-global helpers around brain-core, check their own version surfaces explicitly:

- `src/brain-core/brain_mcp/proxy.py` — bump `PROXY_VERSION` for shipped proxy behaviour changes so upgraded vaults do not report the new proxy as `+modified`.
- `cli/brain` — keep `BRAIN_INSTALL_REF` aligned to `v<src/brain-core/VERSION>` and bump `BRAIN_CLI_VERSION` when the CLI's dispatch or CLI-only behaviour changes.

## When to Update Which Layer

| Change type | Update |
|---|---|
| New MCP tool or change to tool behaviour | `functional/mcp-tools.md` |
| New script or change to script arguments | `functional/scripts.md` |
| End-user install or upgrade contract change | `README.md`, `user/getting-started.md`, `functional/scripts.md`; also bump `src/brain-core/VERSION` using the repo's pre-1.0 semver rules (backward-compatible = patch, breaking = minor) |
| Config, profiles, or merge rules | `functional/config.md` |
| New artefact type, lifecycle, or frontmatter convention | `user/system-guide.md` |
| Template vault defaults change | `user/template-library-guide.md` |
| Install, upgrade, or first-vault steps | `user/getting-started.md` |
| Day-to-day workflow or MCP tool workflow | `user/workflows.md` |
| Plugin install/use in a vault | `user/plugins.md`, `src/brain-core/plugins.md` |
| Plugin authoring or packaging | `contributor/plugins.md`; if the shipped overview changes too, also `src/brain-core/plugins.md` |
| Bootstrap principles or curated core-doc/standards links | `src/brain-core/session-core.md`; if the bootstrap entry flow changes too, also `src/brain-core/index.md` and `src/brain-core/md-bootstrap.md` |
| Architectural decision (non-obvious, cross-cutting) | Add a DD file to `architecture/decisions/` |
| System boundary or security model | `architecture/overview.md` or `architecture/security.md` |

When in doubt, check `docs/README.md` — if a doc file is listed there, it's a canonical reference that may need updating.

## Testing Workflow

Use serial `make test` for the pre-commit gate. `make test-parallel` is a fast
pytest-xdist feedback path while iterating, but it does not replace the serial
run because serial ordering still catches cross-file pollution.

`make lint` is an umbrella for two different documentation contracts. The
legacy/reusable scripts retain a percentage docstring ratchet; internal
`_application` owners are excluded because public-looking dataclass and hook
counts are not an API-quality measure. `lint-command-docs` instead requires
strict described schemas for every command and behavioural docstrings on the
supported `brain_application` kernel/context/result façade. Add useful
invariant or trust-boundary docstrings to that façade; do not add mechanical
one-line restatements to owner `execute`, `decode`, or `catalogue_entry` hooks.

The `Linux test suite` GitHub Actions workflow runs the full `make test` on
`ubuntu-latest`, so the suite must stay host-independent (timezone, filesystem
case-sensitivity, and the Python interpreters on `PATH` are all pinned or
isolated in `tests/conftest.py`).

The `Windows user smoke` GitHub Actions workflow is a narrow user-path guard,
not a contributor-platform promise. It runs `tests/test_windows_user_smoke.py`
and `tests/application/test_mcp_stable_bootstrap.py` on `windows-latest` to exercise
native install, persisted user-command startup and ordinary read round trips.
The smoke's MCP read/upload protocol also runs on macOS and Linux so command
name and authorisation-contract drift is caught before the native install gate.
Keep subprocess pipe readers portable in suites included by Windows smoke;
POSIX `select` cannot establish Windows pipe readiness. Model optional runtime
dependencies explicitly in unit fixtures, and coordinate concurrency tests with
events rather than assuming lock fairness under a tight writer loop.

After an authorised push, follow the
[post-push CI check](../standards/agent-workflow.md#post-push-ci-check).
Include the tested commit and workflow results in the handoff; local test success
does not complete this gate. Use `src/scripts/check_ci.py` for exact-commit
push/manual-dispatch evidence; the committed post-commit hook only reminds and
never runs the remote check automatically.

## Installing for Users

When a user asks an agent working in this repo to install a Brain vault on their behalf, separate the job into two outcomes:

1. Vault scaffold created at the requested path
2. MCP setup completed (central managed runtime at `~/.brain/venvs/` + dependency install + registration)

Do not treat those as all-or-nothing unless the user explicitly requires MCP to be ready immediately.

Preferred command selection:

- Use `bash install.sh --non-interactive --skip-mcp <path>` in restricted, sandboxed, or otherwise uncertain environments.
- Use `bash install.sh --non-interactive --client all <path>` only when package index access is expected to work; replace `all` with an explicitly selected client when appropriate.
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

## Deterministic repository contracts

Work on `dev`. Development commits use `WIP:`, `docs:`, `test:`, or `chore:`
and do not bump `src/brain-core/VERSION`. The pre-commit hook passes
`--policy development` on `dev`, so the staged version-bump predicate is
omitted there, and it reads `.canaries/pre-commit-development.md`. Ordinary
commits on `main` are rejected. A version reaches `main` only through
`src/scripts/promotion.py`: `status`, `prepare --input request.json`,
`finish promotion/vX.Y.Z`, and `discard`. Prepare reads
`.canaries/pre-promotion.md` and removes the receipt `.canary--pre-promotion`
only after the candidate push succeeds. The checklist stays. Finish requires
the candidate's own CI result.

The pre-commit hook runs
`.venv/bin/python src/scripts/check_repository_contracts.py --staged --policy <policy>` before
reading the canary receipt. The checker materialises the Git index and executes
that snapshot's checker and parser imports, so neither staged data nor staged
semantics can be validated by unstaged code. The runner composes purpose-owned
policies under `src/scripts/_repository_contracts/`; both belong to the staged
bootstrap closure. Together they own facts that code can decide:
VERSION/README badge/changelog coupling, DD/index parity and number permanence,
artefact-library metadata/catalogue/count consistency, and documentation
reachability. `make test` exercises the same predicates against the checkout
plus focused failure cases.

Keep subjective review in `.canaries/pre-commit-development.md` for `dev`
commits and `.canaries/pre-promotion.md` for a promotion. The older
`.canaries/pre-commit.md` remains the full release checklist. When a checklist statement
can be expressed as an equality, set comparison, graph reachability rule, or
Git predicate, add it to the checker/tests instead of requiring self-attestation.

Before committing, use `python src/scripts/release.py status` to distinguish
the release facts in `HEAD`, the index and the working tree, then run `make
precommit-check` after staging. That target passes the same branch policy as
the pre-commit hook. The hook never fixes or stages files. For a new
release, provide the chosen Core/CLI/proxy versions and authored changelog facts
to `release.py prepare`; review its dry-run diff before passing `--apply`.

## Why Drift Happens

The same fact often appears in multiple files. For example, "Plans lifecycle is `draft` → `approved` → `implementing` → `completed`" appears in the Plans taxonomy, `docs/user/system-guide.md`, `src/brain-core/guide.md`, and `src/brain-core/artefact-library/README.md`. When a commit updates some but not all, the docs drift.

Deterministic repository contracts catch exact drift. The pre-commit canary
retains the remaining impact review: grep for shared values that do not yet
have a reliable canonical representation and verify every affected occurrence.

## Multi-Repo Workflow

Brain-core is developed here (`src/brain-core/`) and deployed to vaults by copying the whole directory to `.brain-core/`. When changes span brain-core and a vault:

1. Implement and commit core changes in this repo first.
2. When pushing is authorised, push and complete the [post-push CI check](../standards/agent-workflow.md#post-push-ci-check) for that exact commit. Do not propagate a CI-unverified candidate.
3. Follow any local post-core-commit canary (`.canaries/post-core-commit.local.md`) for the separately authorised vault upgrade and documentation steps. Defer propagation while CI is unresolved.
4. Commit in the vault repo when authorised.

Never deploy to both simultaneously. Core-first, always.

Step 3 depends on having a local vault. Post-core-commit canaries are machine-specific and belong in `AGENTS.local.md`, not `AGENTS.md`.

## Common Pitfalls

### Stale install procedures

Install/extension procedures appear in `user/getting-started.md`, `standards/extending/README.md`, and `artefact-library/README.md`. Since v0.9.12, colours are auto-generated — any mention of manual CSS steps or colour picking is stale.

### Type table vs defaults

The quick-start guide (`src/brain-core/guide.md`) type table should show template vault defaults. The artefact library README shows all available types. These are different lists — don't copy one into the other.

### Template-vault drift is not just file content

When artefact-library definitions change, matching `template-vault/_Config/`
content alone is not enough. `.brain/tracking.json` may still record stale
installed hashes, which means the template vault is not truly clean even if the
rendered files already match upstream. Use `make sync-template-check` to verify
state, and `make sync-template` to refresh both `_Config/` and tracking.

### When to update guide.md

The quick-start guide (`src/brain-core/guide.md`) ships in every vault and should be updated when:
- New artefact types are added to the template vault defaults
- Core conventions change (naming, frontmatter, filing)
- New user-facing tooling is introduced
- Workflows are added or modified

## Running tests from a `/tmp` worktree

Some tests (e.g. the install/upgrade path-validation suite) use a `non_tmp_vault`
fixture that asserts the vault root does **not** live under the system temp
directory. When the working tree itself is a git worktree under `/tmp/` (common
with `git worktree add /tmp/<branch>`), that assertion fails with confusing
messages because `pytest`'s `tmp_path` also resolves inside `/tmp`.

Set the `BRAIN_TEST_NON_TMP_ROOT` environment variable to an absolute path
outside `/tmp` / `/private/tmp` before running the suite:

```bash
BRAIN_TEST_NON_TMP_ROOT=$HOME/.cache/brain-test-non-tmp make test
```

The fixture at `tests/conftest.py::non_tmp_vault` reads this override and uses
it as the parent directory for the non-temp vault. No override is needed when
the working tree lives outside `/tmp` (the default `~`/repo-local path is
already outside the temp root).
