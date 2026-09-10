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
configured via `brain retrieval enable --vault /path/to/brain --json`.
That flow writes the local semantic flags first, then provisions the pinned
runtime packages, snapshots the pinned model under
`.brain/local/semantic-models/`, records
`.brain/local/semantic-model-manifest.json`, and refreshes semantic sidecars.
Ordinary semantic runtime paths then load from that local snapshot only. The
encoder runs the snapshot's ONNX export through `onnxruntime` on the CPU with
the `tokenizers` library; no torch, GPU or accelerator is involved, so a
long-lived MCP server that answers semantic queries stays under about 200 MB.

Example local opt-in:

```yaml
defaults:
  flags:
    semantic_processing: true
    semantic_retrieval: true
```

- `semantic_processing` enables embedding-backed `content.classify`,
  `content.resolve`, and `content.ingest` behaviour. When false, degraded
  non-embedding modes remain available.
- `semantic_retrieval` enables semantic and hybrid artefact search. When true,
  `artefact.search` accepts `mode="semantic"` and `mode="hybrid"`, and omitted
  `mode` defaults to hybrid when the embeddings sidecars and dependencies are
  available.
- `defaults.local_runtime.semantic_engine_installed` is a machine-local marker
  written by `retrieval.enable` or repaired by
  `retrieval.repair-semantic`. It flips true only after the configured vault has the
  pinned runtime packages, the pinned local model snapshot, and refreshed
  sidecars; the runtime still re-checks dependencies, manifest/model load, and
  sidecar provenance before using the semantic engine.
- The shipped template default is `false`; enabling it is an explicit opt-in.
- When either flag is enabled, `build_index.py` and the MCP server may refresh
  the optional `.brain/local/type-embeddings.npy`,
  `.brain/local/doc-embeddings.npy`, and `.brain/local/embeddings-meta.json`
  sidecars on demand.

### Startup behaviour

On startup, the MCP server probes the three config inputs, loads them through the same shared Brain-owned YAML seam as `load_config()`, runs the merge, validates the result (unknown profile tool names raise warnings), and publishes the merged config into the long-lived server runtime. Subsequent calls reuse that identity until an input file signature changes.

Config freshness is checked before profile enforcement and before `session.start` authentication. Missing optional vault/local config files are treated as `{}`; malformed or unreadable YAML is a config error. While a config error is active, granular MCP commands fail closed. The last good config remains in memory internally, but runtime readers do not use it again until a later config signature change reloads cleanly.

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

`workspace.bind` may scaffold `.brain/local/workspace.yaml`, but the file remains human-editable and is expected to evolve over time.
`workspace.repair-registry` is intentionally narrower: it repairs or normalises `.brain/local/workspaces.json` only, not the human-owned workspace manifest.

---

## Authority Profiles

**Design decisions:** [DD-025](../architecture/decisions/dd-025-privilege-split.md), [DD-059](../architecture/decisions/dd-059-attachment-upload-boundary.md)

The config system supports five cumulative built-in profiles with user-centred levels of access:

| Profile | Intended use |
|---------|-------------|
| `reader` | Inspect, discover and manage access state |
| `contributor` | Reader access plus ordinary content creation, editing and lifecycle work |
| `maintainer` | Contributor access plus definition, plugin and derived-index maintenance |
| `operator` | Maintainer access plus workspace registration and runtime-operational changes |
| `administrator` | Operator access plus irreversible artefact deletion |

Each profile has a per-tool allow-list defined in the vault config. Tools not on the active profile's allow-list return an error `CallToolResult` — no silent failures.

Brain Core derives its built-in lists from the authoritative catalogue's
authority metadata. The exact installed counts are generated and tested rather
than maintained as a prose contract; `command.list` and MCP discovery expose
the applicable ceiling for a selected installation. A known denied leaf is
rejected from catalogue plus trusted profile state before dynamic request
resolution, executor entry or effects. There is no aggregate name fallback.

The 0.55.0 upgrade performs a one-time, fail-closed profile migration. Exact
historical three-profile built-ins become the five catalogue-derived sets above. A custom profile
expands only the legacy tools it explicitly allowed, preserves its other
metadata and gains `invocation.read` only when a mapped mutator requires
receipt-backed recovery. Mixed granular/legacy input is idempotent; unknown
tools or malformed definitions fail the whole migration before output. The
migration is part of the checked upgrade transaction.

The same migration rewrites exact Brain-shipped `brain_session` bootstrap text
in root `AGENTS.md`/`Agents.md` and `CLAUDE.md` to `session.start`, even when no
shared profile config exists. Custom prose is untouched, and profile validation
finishes before either config or bootstrap output is written.

### Active access

The authenticated profile is the command ceiling. Active access is configured separately:

```yaml
vault:
  access:
    elevation_policy: automatic  # automatic | external | denied
    default_lease_seconds: 900
    max_lease_seconds: 3600
    pending_seconds: 900
    max_use_count: 100
defaults:
  access:
    initial_profile: reader
```

The shared `vault.access` policy cannot be overridden by machine-local config. `defaults.access.initial_profile` is locally customisable, but its commands are intersected with the authenticated ceiling. Leases are principal-scoped, exact, absolutely expiring and stored under `.brain/local/access-state.json`; `access.status` is read-only when that file does not exist. `external` policy requires a registered operator key supplied out of band to `brain access approve`; the approving operator profile must cover the requested commands.

### Authentication

The MCP composition root accepts `BRAIN_OPERATOR_KEY` as trusted server configuration; the CLI accepts `--operator-key` as adapter input. Before composing invocation authority, Brain refreshes config, hashes the supplied key with SHA-256 and matches it against registered operators in the vault config. On a match, the invocation uses the operator's configured profile. If no key is supplied, the default profile is used. Operator identity is never a semantic request field.

Every command is authorised before request resolution and executor entry. If config is malformed, unreadable, or names an unknown profile/tool, enforcement fails closed. `session.start` remains available as the explicit authentication/bootstrap command; it does not create a compatibility session state for removed aggregate tools.

### Generating a key

Use `src/brain-core/scripts/generate_key.py` to create a new operator key:

```bash
python3 generate_key.py
# Prints: key (store securely) + SHA-256 hash (put in config)
```

The key is a random secret; the hash goes in `.brain/config.yaml`; the key goes in `.brain/local/config.yaml` (gitignored) or a secrets manager.

---

## Core Skills

**Design decisions:** [DD-024](../architecture/decisions/dd-024-core-skills.md), [DD-068](../architecture/decisions/dd-068-git-backed-skill-sources-and-managed-exposure.md)

Skills live in two places:

| Location | Source tag | Editable? |
|----------|-----------|-----------|
| `.brain-core/skills/*/SKILL.md` | `"source": "core"` | No — overwritten on upgrade |
| `_Config/Skills/*/SKILL.md` | `"source": "user"` | Yes |

The compiler discovers both locations and retains both rows in the compiled
router. Unqualified names resolve user-first; `user:<name>` and `core:<name>`
select one substrate explicitly. Core skills are tagged `"source": "core"` and
user skills `"source": "user"`.

Core skills teach agents how to use brain-core's own tools. They ship in `.brain-core/` and are intentionally overwritten on upgrade — they describe system methodology, not user configuration.

A supported edit of a core-only skill first creates the identical package under
`_Config/Skills/`, then applies the edit there. Core remains immutable. A clean
tracked copy is collapsed back to core during a later Brain upgrade when its
complete package identity exactly matches the upgraded core package.

### Git-backed sources

Git provenance is optional and independent of core/user ownership. Bundled
source descriptors live in `.brain-core/skill-sources.json`; user tracking and
baselines live in `.brain/skill-sources.json`.

An unscoped refreshing status request acquires each distinct repository and ref
once, then stages and validates every configured skill path independently. One
invalid package therefore cannot hide valid sibling results, while repositories
shared by several skills do not incur repeated serial fetches.

Application-facing Git acquisition accepts only explicit HTTPS, SSH URL, or
SCP-style SSH repository locations. Local paths, `file://` repositories,
option-shaped refs and refs containing Git revision expressions are rejected at
the request boundary. A machine-local repository is therefore not readable
through contributor-level MCP authority.

```bash
brain skill add-git --request-json \
  '{"repository":"https://github.com/example/skills.git","skill_path":"skills/example","configured_ref":"main"}' --json
brain skill list --request-json '{}' --json
brain skill status --request-json '{"name":"example"}' --json
brain skill update --request-json '{"name":"example"}' --json
brain skill detach --request-json '{"name":"example"}' --json
```

An update always targets an existing user package first. If none exists and a
core source descriptor is available, `skill.update` installs the updated package
as a user skill. It never mutates core. Local and upstream divergence stages the
upstream package plus `comparison.json` under
`.brain/skill-conflicts/<name>/`. Preserve the current package, detach it,
manually reconcile then update to rebaseline, or explicitly replace it with
`{"replace_conflict":true}`; replacement archives the local package under
`.brain/skill-backups/`.

### Client discovery adapters

Claude Code and Codex discover native skills in global and project directories.
Brain can explicitly expose any valid effective skill through a thin adapter
without duplicating the package:

```bash
brain skill expose --vault /path/to/brain \
  --request-json '{"name":"shaping","client":"all","scope":"global"}' --json
brain skill expose --workspace /path/to/bound/project \
  --request-json '{"name":"shaping","client":"codex","scope":"project"}' --json
brain skill unexpose --request-json \
  '{"name":"shaping","client":"all","scope":"global"}' --json
```

Each adapter calls `session.start`, then reads the unqualified effective skill
through `resource.read`. A same-name user package therefore takes precedence
without changing the exposure. Relative package files are read from the returned
core/user substrate. Global adapters use the active-Brain resolution ladder,
including the configured machine default. Project adapters resolve an existing
canonical workspace binding first and otherwise use the configured machine
default; exposure never creates or changes a binding.

Each installed adapter has a Brain ownership marker and content digest. Re-running
the command updates only an unmodified Brain-owned adapter. An unmanaged skill is
left untouched unless `"replace":true` is supplied, in which case the complete old
directory is moved outside skill discovery to
`~/.<client>/.brain-skill-backups/<name>.pre-brain-adapter[-N]`.
`skill.unexpose` likewise removes only an unmodified Brain-owned adapter. Symlinked targets,
including the backup root, and unexpected files are refused. These writes are
never performed implicitly during vault upgrade; restart the affected client
after an explicit command reports a change.
The older `agent-skill.configure` shaping command remains compatible. Generic
`skill.expose` is the normal surface for new policy.

### Current core skills

Portable multi-workflow families expose exactly one public `<family>/SKILL.md`.
That root routes directly to frontmatter-free, one-level
`<family>/references/*.md` workflow files so Agent Skills clients do not
rediscover colon-named nested skills.

- `shaping` — one Brain-owned public entry point that composes an exact vendored
  portable workflow with `references/brain.md`; the portable source owns shaping
  behaviour, while the Brain adaptor contributes independently selected target,
  persistence, taxonomy, provenance, and lifecycle capabilities. Installed
  vaults have no runtime dependency on the source repository.
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
.venv/bin/pip install -r src/brain-core/brain_mcp/requirements.txt "pytest>=9.0" "pytest-bdd>=8.0" "pytest-xdist>=3.6" "interrogate>=1.7" "pytest-cov>=6.0"
.venv/bin/pytest -q
```

### Test configuration

`pyproject.toml` configures pytest with `pythonpath` entries for `src/brain-core` and `src/brain-core/scripts`, so test files can `import check` and `from brain_mcp import server` without `sys.path` manipulation.
