# Configuration and Development

Reference for the brain-core configuration system, operator profiles, core skills, pending design work, and development setup.

**See also:**
- [docs/functional/mcp-tools.md](mcp-tools.md) — MCP tool specifications and server details
- [docs/architecture/decisions/](../architecture/decisions/) — design decisions referenced throughout

## Configuration System

The MCP server loads vault configuration via a three-layer merge on startup:

1. **Template defaults** — `defaults/config.yaml` shipped with brain-core. Provides all valid keys and fallback values.
2. **Vault config** — `.brain/config.yaml` (shared, committed). Shared authority for the vault. Overrides template defaults.
3. **Local overrides** — `.brain/local/config.yaml` (machine-local, gitignored). Personal overrides that must not be committed (e.g. local paths, personal preferences).

### Zones

The merged config has two zones with different override rules:

**`vault` zone — shared authoritative.** Template defaults, then vault overrides. Local config cannot touch this zone — any `vault` keys in `.brain/local/config.yaml` are silently ignored with a warning. Use this zone for settings that must be consistent across all machines (brain name, artefact type definitions, operator profiles).

**`defaults` zone — type-based merge.** Template, then vault, then local, using type-aware rules:
- **Scalars:** local wins if present.
- **Booleans:** either-true wins (opt-in flags stay on once set).
- **Lists:** additive union (order-preserving, deduplicated).
- **Dicts:** recursive merge.

### Paths

```
defaults/config.yaml          # template (shipped with brain-core)
.brain/config.yaml            # vault-level overrides (commit this)
.brain/local/config.yaml      # machine-local overrides (gitignored)
```

The loader locates the template relative to the script file, so it works both from the dev repo (`src/brain-core/scripts/` → `src/brain-core/defaults/`) and from an installed vault (`.brain-core/scripts/` → `.brain-core/defaults/`).

Brain uses a shared Brain-owned YAML subset for these standalone config/workspace files. It supports the shapes Brain actually uses (mappings, lists, booleans, integers, empty collections, quoted/plain strings) and rejects unsupported general-YAML features such as anchors, merge keys, tags, and block scalars.

### Local tool paths

Tool-backed workflows can resolve machine-local binaries from the `defaults` zone before falling back to `PATH`. This is the current short-term contract for tools such as `pandoc` and TeX engines.

Example:

```yaml
defaults:
  tool_paths:
    pandoc: /opt/homebrew/bin/pandoc
    xelatex: /Library/TeX/texbin/xelatex
```

These values belong in `.brain/local/config.yaml`, not `.brain/config.yaml`, because they are machine-local installation details. Environment variables can still override them when a specific runtime launch needs a different binary path.

### Feature flags

Feature flags live under `defaults.flags`. They are off by default in the
shipped template and follow the normal defaults-zone merge rules, so a local
override can opt in without changing the shared vault config.

Semantic and hybrid retrieval also require the optional semantic runtime
configured via `python3 .brain-core/scripts/configure.py semantic --enable`.
That flow writes the local semantic flags first, then provisions the pinned
runtime packages, snapshots the pinned model under
`.brain/local/semantic-models/`, records
`.brain/local/semantic-model-manifest.json`, and refreshes semantic sidecars.
Ordinary semantic runtime paths then load from that local snapshot only. Intel
macOS remains unsupported for semantic runtime provisioning and should stay on
lexical search.

Example local opt-in:

```yaml
defaults:
  flags:
    semantic_processing: true
    semantic_retrieval: true
```

- `semantic_processing` enables embedding-backed `brain_classify`,
  `brain_resolve`, and `brain_ingest` behaviour. When false, degraded
  non-embedding modes remain available.
- `semantic_retrieval` enables semantic and hybrid artefact search. When true,
  `brain_search` accepts `mode="semantic"` and `mode="hybrid"`, and omitted
  `mode` defaults to hybrid when the embeddings sidecars and dependencies are
  available.
- `defaults.local_runtime.semantic_engine_installed` is a machine-local marker
  written by `configure.py semantic --enable` or repaired by
  `repair.py semantic`. It flips true only after the configured vault has the
  pinned runtime packages, the pinned local model snapshot, and refreshed
  sidecars; the runtime still re-checks dependencies, manifest/model load, and
  sidecar provenance before using the semantic engine.
- The shipped template default is `false`; enabling it is an explicit opt-in.
- When either flag is enabled, `build_index.py` and the MCP server may refresh
  the optional `.brain/local/type-embeddings.npy`,
  `.brain/local/doc-embeddings.npy`, and `.brain/local/embeddings-meta.json`
  sidecars on demand.

### Startup behaviour

On startup, the MCP server probes the three config inputs, loads them through the same shared Brain-owned YAML seam as `load_config()`, runs the merge, validates the result (unknown profile tool names raise warnings), and publishes the merged config into the long-lived server runtime.

Config freshness is also checked mid-session before profile enforcement and before `brain_session` authentication. Missing optional vault/local config files are treated as `{}`; malformed or unreadable YAML is a config error. While a config error is active, guarded MCP tools fail closed and `brain_init(debug=true)` reports `debug.config_error`. The last good config remains in memory internally, but runtime readers do not use it again until a later config signature change reloads cleanly.

---

## Workspace Manifest

Workspace metadata is intentionally separate from the Brain config system.

`.brain/local/workspace.yaml` is an optional, workspace-owned declaration that lives in a connected workspace folder, not in the Brain vault itself. It is used for workspace identity, links to canonical Brain artefacts, and filing defaults such as auto-tags.

This file does **not** participate in the three-layer config merge above. That merge is reserved for Brain/vault configuration:

1. `defaults/config.yaml`
2. `.brain/config.yaml`
3. `.brain/local/config.yaml`

The manifest lives in `.brain/local/` because every field describes the relationship between a specific clone and a specific vault — slug, brain identity, artefact links, and auto-tags are all install-specific. It uses YAML (not JSON) because it is human-authored and declarative; the location follows from its content being machine-local, not from its authorship model.

The distinction from Brain config:

- `.brain/config.yaml` is Brain-level shared configuration
- `.brain/local/workspace.yaml` is workspace-level identity and defaults (machine-local)
- `.brain/local/workspaces.json` is machine-local binding state for linked workspaces

Tooling such as `setup.py workspace` or `configure.py workspace binding` may scaffold `.brain/local/workspace.yaml`, but the file remains human-editable and is expected to evolve over time.
`repair.py registry` is intentionally narrower: it repairs or normalises `.brain/local/workspaces.json` only, not the human-owned workspace manifest.

---

## Operator Profiles

**Design decisions:** [DD-025](../architecture/decisions/dd-025-privilege-split.md), [DD-059](../architecture/decisions/dd-059-attachment-upload-boundary.md)

The config system supports three built-in operator profiles with different levels of access:

| Profile | Intended use |
|---------|-------------|
| `reader` | Read-only access, including `brain_outline`, `brain_check`, `brain_classify`, and `brain_resolve` |
| `contributor` | Read + attachment upload + create/edit/lifecycle and `brain_ingest` |
| `operator` | Full access including guarded `brain_define`, `brain_move`, and `brain_action` |

Each profile has a per-tool allow-list defined in the vault config. Tools not on the active profile's allow-list return an error `CallToolResult` — no silent failures.

The staged command interface derives its future built-in lists from the
authoritative catalogue's MCP eligibility and authority metadata: reader has 41
exact leaves, contributor cumulatively has 82, and operator has all 109. A
known denied leaf is rejected from catalogue plus trusted profile state before
dynamic request resolution, executor entry or effects. Existing aggregate
defaults remain public until the coordinated breaking cutover migrates both
built-in and custom allow-lists; there is no runtime aggregate fallback.

v0.54.56 stages that one-time migration as a pure pre-write operation. Exact
historical built-ins become the catalogue-derived sets above. A custom profile
expands only the legacy tools it explicitly allowed, preserves its other
metadata and gains `invocation.read` only when a mapped mutator requires
receipt-backed recovery. Mixed granular/legacy input is idempotent; unknown
tools or malformed definitions fail the whole migration before output. The
migration is not activated until the coordinated cutover transaction.

### Authentication

`brain_session` accepts an optional `operator_key` parameter. Before authenticating, the server refreshes config if any config input changed. It then hashes the supplied key with SHA-256 and matches it against registered operators in the vault config. On a match, it sets the session profile to the operator's configured profile for all subsequent per-call enforcement. If `operator_key` is omitted, the default profile from config is used.

All tools except `brain_session` itself enforce the active profile. If config is malformed or unreadable, enforcement fails closed and guarded tools return the config error. If the active session profile is removed from config while the server is running, guarded tools return an error asking the operator to run `brain_session` again or fix config. Vaults with fresh config but no active session profile still run without per-call enforcement, which preserves the unauthenticated bootstrap path.

### Generating a key

Use `src/brain-core/scripts/generate_key.py` to create a new operator key:

```bash
python3 generate_key.py
# Prints: key (store securely) + SHA-256 hash (put in config)
```

The key is a random secret; the hash goes in `.brain/config.yaml`; the key goes in `.brain/local/config.yaml` (gitignored) or a secrets manager.

---

## Core Skills

**Design decisions:** [DD-024](../architecture/decisions/dd-024-core-skills.md)

Skills live in two places:

| Location | Source tag | Editable? |
|----------|-----------|-----------|
| `.brain-core/skills/*/SKILL.md` | `"source": "core"` | No — overwritten on upgrade |
| `_Config/Skills/*/SKILL.md` | `"source": "user"` | Yes |

The compiler discovers both locations and merges them into the compiled router. Core skills are tagged `"source": "core"` in the router; user skills are tagged `"source": "user"`. This lets agents and tools distinguish system methodology from vault-specific configuration.

Core skills teach agents how to use brain-core's own tools. They ship in `.brain-core/` and are intentionally overwritten on upgrade — they describe system methodology, not user configuration.

### Client discovery adapters

Claude Code and Codex discover native skills in separate machine-global
directories. Brain can install the same thin `shaping` adapter into both without
duplicating the actual workflow:

```bash
brain configure agent-skills --vault /path/to/brain --client all
```

The checked-in adapter template at
`.brain-core/client-adapters/shaping/SKILL.md` calls `brain_session`, then reads the authoritative
`.brain-core/skills/shaping/SKILL.md` from the active Brain with `brain_read`.
Consequently the client-visible workflow and its MCP contract come from the same
Brain version. Only the stable adapter is installed under
`~/.claude/skills/shaping/` or `~/.codex/skills/shaping/`.

Each installed adapter has a Brain ownership marker and content digest. Re-running
the command updates only an unmodified Brain-owned adapter. An unmanaged skill is
left untouched unless `--replace` is supplied, in which case the complete old
directory is moved outside skill discovery to
`~/.<client>/.brain-skill-backups/shaping.pre-brain-adapter[-N]`. `--remove`
likewise removes only an unmodified Brain-owned adapter. Symlinked targets,
including the backup root, and unexpected files are refused. These writes are
never performed implicitly during vault upgrade; restart the affected client
after an explicit command reports a change.
When that checked-in discovery template is introduced or modified, `upgrade.py`
surfaces the configuration command as a recommended follow-up. Updates to the
authoritative shaping workflow itself need no client update and produce no prompt.

### Current core skills

- `shaping` — parent router plus `assess`, `brainstorm`, `discover`, and `refine` sub-skills for artefact shaping workflows
- `swarm-test` — parent router plus `review` and `evaluate` sub-skills for multi-agent test workflows
- `code-review` — parent router plus `investigate` and `fix` sub-skills for review-only and review-with-fixes workflows
- `software-design-principles` — lightweight reference skill for in-the-moment design decisions and trivial code evaluation
- `software-design-review` — multi-agent design review skill for complex code, diffs, and proposed technical changes

---

## Pending Design

The following items are accepted but not yet fully shaped. Listed here for contributor awareness.

- **CLI wrapper** — argument parsing, vault discovery, distribution
- **Plugin registry** — `plugins.json` schema, install flow
- **Obsidian plugin** — TypeScript implementation, shared test fixtures (DD-005, DD-006, DD-007)
- **Frontmatter timestamps absorption** (DD-004) — ignore rules, agent-aware stamping
- **Procedures directory** — `.brain-core/procedures/`, structured step-by-step instructions for agents without code execution
- **Init wizard** — interactive setup for new users. Includes a vault archetype library (e.g. "Personal Knowledge Base", "Writing Studio", "Software Project") — each archetype bundles a curated set of types as a starting point, with the option to customise after selection

---

## Development Setup

### Prerequisites

- Python 3.12+ (the user-facing install/init/upgrade + MCP runtime contract; contributor tooling also uses 3.12)
- `make` (standard on macOS/Linux)

### Setup

```bash
make install    # creates .venv with Python 3.12, installs mcp + pytest
make test       # runs the full test suite
make clean      # removes .venv and caches
```

Manual setup:

```bash
python3.12 -m venv .venv
.venv/bin/pip install "mcp>=1.0.0" "pytest>=9.0" "pytest-bdd>=8.0" "pytest-xdist>=3.6" "interrogate>=1.7" "pytest-cov>=6.0"
.venv/bin/pytest -q
```

### Test configuration

`pyproject.toml` configures pytest with `pythonpath` entries for `src/brain-core` and `src/brain-core/scripts`, so test files can `import check` and `from brain_mcp import server` without `sys.path` manipulation.
