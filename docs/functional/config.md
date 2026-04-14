# Configuration and Development

Reference for the brain-core configuration system, operator profiles, core skills, pending design work, and development setup.

**See also:**
- [docs/functional/mcp-tools.md](mcp-tools.md) — MCP tool specifications and server details
- [docs/architecture/decisions/](../architecture/decisions/) — design decisions referenced throughout

---

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

### Startup behaviour

On startup, the MCP server calls `load_config()`, which reads all three layers, runs the merge, validates the result (unknown profile tool names raise warnings), and returns a typed dict. If a layer's YAML is missing or unparseable, it is treated as `{}` with a warning — the server continues with the remaining layers.

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

Tooling such as `init.py` may scaffold `.brain/local/workspace.yaml`, but the file remains human-editable and is expected to evolve over time.

---

## Operator Profiles

**Design decisions:** [DD-025](../architecture/decisions/dd-025-privilege-split.md)

The config system supports three built-in operator profiles with different levels of access:

| Profile | Intended use |
|---------|-------------|
| `reader` | Read-only access — `brain_session`, `brain_read`, `brain_search`, `brain_list` |
| `contributor` | Read + create/edit — adds `brain_create`, `brain_edit`, `brain_process` |
| `operator` | Full access — all tools including `brain_action` |

Each profile has a per-tool allow-list defined in the vault config. Tools not on the active profile's allow-list return an error `CallToolResult` — no silent failures.

### Authentication

`brain_session` accepts an optional `operator_key` parameter. The server hashes the supplied key with SHA-256 and matches it against registered operators in the vault config. On a match, it sets the session profile to `operator` for all subsequent per-call enforcement. If `operator_key` is omitted, the default profile from config is used.

All tools except `brain_session` itself enforce the active profile. Vaults with no config loaded have no enforcement — this is intentional backward compatibility.

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

### Current core skills

No core skills ship as of v0.24.0. The `brain-remote` skill was retired — its workflow is now handled by `brain_session` and the SessionStart hook.

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

- Python 3.10+ (scripts target 3.8+ stdlib for portability, but the `mcp` SDK and modern type syntax require ≥3.10)
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
.venv/bin/pip install "mcp>=1.0.0" "pytest>=9.0"
.venv/bin/pytest -q
```

### Test configuration

`pyproject.toml` configures pytest with `pythonpath` entries for `src/brain-core` and `src/brain-core/scripts`, so test files can `import check` and `from brain_mcp import server` without `sys.path` manipulation.
