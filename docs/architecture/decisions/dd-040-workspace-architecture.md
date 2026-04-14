# DD-040: Workspace architecture — hub, registry, manifest, session wiring

**Status:** Implemented (v0.27.5)
**Extends:** DD-038, DD-023

## Context

DD-038 introduced a unified session bootstrap model (`brain_session`) that gives agents everything they need to work with a vault in one call. The bootstrap payload includes environment, artefacts, preferences, and triggers — but it had no concept of *which install* was calling. A vault can be connected to many workspaces (repos, project folders, data pipelines) across one or more machines. Without workspace identity in the session, agents could not:

- Auto-tag artefacts created from a specific workspace.
- Resolve a workspace slug to its data folder.
- Distinguish sessions originating from different projects.

DD-023 established `init.py` as the setup script for MCP registration. It writes `.mcp.json` with environment variables (`BRAIN_VAULT_ROOT`, `PYTHONPATH`) and a SessionStart hook. The workspace feature extends this surface to carry workspace identity through the same channels.

## Decision

### 1. Hub pattern — `Workspaces/{slug}.md`

Each workspace gets a living artefact hub in the vault. The hub connects vault artefacts (research, designs, decisions) to the bounded container of working files that lives outside the vault's artefact taxonomy. The workspace tag (`workspace/{slug}`) is the query mechanism; the hub is the index.

Hub frontmatter declares `workspace_mode: embedded | linked` and carries the workspace tag.

### 2. Data folder — `_Workspaces/{slug}/` (embedded mode)

For workspaces whose data lives inside the vault, `_Workspaces/{slug}/` is a freeform data bucket. Files inside are not brain artefacts — no frontmatter, no naming conventions, no taxonomy rules. The brain does not index or enforce conventions inside `_Workspaces/`.

### 3. Registry — `.brain/local/workspaces.json` (machine-local)

Linked workspaces (data outside the vault) register their slug-to-path mapping in `.brain/local/workspaces.json`. This file is machine-local and gitignored — the vault remains portable. Resolution order: embedded first (`_Workspaces/{slug}/` exists), then linked (registry lookup).

### 4. Manifest — `.brain/local/workspace.yaml` (workspace-owned, machine-local)

A workspace may declare its own identity via `.brain/local/workspace.yaml` in the workspace folder. This file carries the workspace slug, filing defaults (auto-tags), and optional links back to the vault hub. It is workspace-owned: `init.py` scaffolds a minimal version on setup, but the file is human-editable and expected to evolve.

The manifest lives in `.brain/local/` because every field describes the relationship between a specific clone and a specific vault — slug, brain identity, artefact links, and auto-tags are all install-specific. A different developer connecting to a different brain would have different values. The file uses YAML (not JSON) because it is human-authored and declarative; the `.brain/local/` location follows from its content being machine-local, not from its authorship model.

### 5. Session wiring — `BRAIN_WORKSPACE_DIR` is always local-install scope

`BRAIN_WORKSPACE_DIR` is an environment variable set by `init.py` into the MCP server configuration (`.mcp.json` env block) and the SessionStart hook command (`session.py --workspace-dir ...`). It identifies the specific install — the workspace folder on this machine — that is calling the brain.

This value is always a local-install concern:

- It is an absolute path specific to the machine where the workspace is installed.
- It belongs alongside `BRAIN_VAULT_ROOT` and `PYTHONPATH` in the MCP env block — all three are machine-local paths.
- Whether the file carrying these values (`.mcp.json`) is committed to version control is the vault's or workspace's choice. Brain makes no claim about the commit status of `.mcp.json`. Some vaults commit it (single-machine vaults where portability is irrelevant); others gitignore it (shared repos where absolute paths differ per machine).

When `BRAIN_WORKSPACE_DIR` is set, `brain_session` resolves it to a workspace record (slug, mode, hub path) and includes optional workspace defaults (auto-tags from the manifest). When unset, bootstrap degrades cleanly to generic vault context — workspace awareness is additive.

## Alternatives Considered

**Store the workspace registry in `.brain/config.yaml` (vault shared config).** Rejected: the registry maps slugs to absolute paths that are inherently machine-specific. The three-layer config merge (DD-032) is designed for shared-authoritative settings in the vault zone; machine-local paths belong in `.brain/local/`.

**Store `BRAIN_WORKSPACE_DIR` in `.brain/config.yaml` defaults zone.** Rejected: the env var identifies the caller, not the vault. It is a property of the install, not of the brain configuration. Placing it in the config merge would conflate two ownership domains.

**Make `.mcp.json` portable via environment variable expansion.** Rejected: Claude Code's `.mcp.json` loader does not perform env-var substitution. Achieving portability would require a shim or wrapper that Brain does not control. The pragmatic answer is that `.mcp.json` holds machine-local absolute paths today (`command`, `BRAIN_VAULT_ROOT`, `PYTHONPATH`) and `BRAIN_WORKSPACE_DIR` is consistent with that existing model.

**Fold workspace scoping into DD-038 or DD-039.** Rejected: workspaces are a substantial feature spanning hub artefacts, a registry, a manifest, and session wiring. The scoping contract is one part of a larger architectural surface that warrants its own decision record.

## Consequences

**Positive:**
- Explicit scope contract: `BRAIN_WORKSPACE_DIR` is unambiguously local-install, ending confusion about what belongs in shared vs machine-local config.
- Hub pattern connects vault artefacts to external working files without forcing those files into the artefact taxonomy.
- Machine-local registry keeps the vault portable — moving or syncing the vault to another machine does not carry stale absolute paths.
- Session wiring is additive — workspaces enhance bootstrap when present, and bootstrap degrades cleanly when absent.

**Negative:**
- More files to understand: hub, registry, manifest, and the env var form a four-part surface.
- `.mcp.json` commit status remains a per-vault/per-workspace decision. Brain cannot enforce the right choice for all use cases.
- Embedded and linked modes share a slug namespace — an embedded workspace blocks registration of a linked workspace with the same slug.

## Implementation Notes

- `workspace_registry.py` — slug resolution, embedded scanning, linked registration, CLI.
- `session.py:_workspace_summary()`, `_resolve_workspace_record()`, `_load_workspace_manifest()`, `_extract_workspace_defaults()` — session model assembly.
- `init.py:ensure_workspace_manifest()` — scaffolds `.brain/local/workspace.yaml` for folder-scoped setups.
- `init.py:build_mcp_config()` — writes `BRAIN_WORKSPACE_DIR` into `.mcp.json` env when `workspace_dir` is supplied.
- `_Config/Taxonomy/Living/workspaces.md` — artefact type definition for workspace hubs.
