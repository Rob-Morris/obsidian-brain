# Configuration and Development

Reference for the brain-core configuration system, operator profiles, core skills, pending design work, and development setup.

**See also:**
- [docs/functional/mcp-tools.md](mcp-tools.md) — MCP tool specifications and server details
- [docs/architecture/decisions/](../architecture/decisions/) — design decisions referenced throughout

## Configuration System

Machine-level [managed client approvals](approvals.md) are a separate opt-in
concern. They do not belong in the synced vault configuration and never alter
Brain credential ceilings or exceptional consent.

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

Brain uses a shared Brain-owned YAML subset for these standalone config/workspace files. It supports the shapes Brain actually uses (mappings, lists, booleans, integers, empty collections, quoted/plain strings) and rejects unsupported general-YAML features such as anchors, merge keys, tags, and block scalars. Files are read as UTF-8; one leading byte-order mark (U+FEFF), which some editors add, is accepted and ignored, so a marked `.brain/config.yaml` parses exactly like an unmarked one. Markdown frontmatter is read the same way: one leading mark before the opening `---` is accepted, and a rewrite of the file drops it.

A config decode failure raises `ConfigError` naming the affected
`.brain/config.yaml` or `.brain/local/config.yaml`, its text diagnosis (such as
`utf16_bom` or `not_utf8`), and the remedy: convert the file to UTF-8 in an
editor. Config YAML is not part of the vault text-file scan: all commands need
config to load before they can check or repair the vault.

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

## Machine and workspace state

Brain keeps a few machine-local and workspace-local files that record which
Brains exist on this machine, which one is the default, which folders link to
which Brain, and which MCP routes Brain installed. Each fact has one home. A
file is either a source of truth or derived from one, and a derived file is
written by the operation that changes the fact and otherwise re-derived from
its source by a check with an automatic repair
([DD-083](../architecture/decisions/dd-083-one-home-per-register.md)). No file
here is part of the three-layer config merge above. `~/.config` stands for the
machine config-home, which follows `XDG_CONFIG_HOME` (or the native Windows
application-data location).

| File | Name | Nature | Written by |
|---|---|---|---|
| `~/.config/brain/vaults` | vault registry | Source of truth: the local Brains and their IDs. "Brain registration" means a row here | `vault_registry.py`, reached from `install.py`, the launcher's `brain register`, `brain unregister` and `brain registry remove-stale`, and the direct `vault_registry.py` script |
| `~/.config/brain/default` | default Brain pointer | Source of truth: the opt-in machine default | `vault_registry.py`, reached from a user-scope install, `brain set-default` and `brain clear-default`, and cleared by `brain unregister`, `brain uninstall` and `brain registry remove-stale` when they remove the default Brain |
| `~/.config/brain/mcp-registrations.json` and `<vault>/.brain/local/init-state.json` | MCP registration ledger (user claims, and project or local claims) | Source of truth: the MCP routes Brain installed and owns. "MCP registration" means a record here | MCP configuration, repair and migration (see [MCP ownership and migration](#mcp-ownership-and-migration)) |
| `<workspace>/.brain/local/workspace.yaml` | workspace manifest | Source of truth of the workspace link (`brain` and `links.workspace`), plus the local `slug`, defaults and tags | `workspace.setup` and `workspace.unregister` for the link fields; people for the rest |
| `<vault>/.brain/local/workspaces.json` | linked workspace registry | Derived: hub key to folder, for the manifests that name this Brain. It is kept because a Brain cannot enumerate manifests | `workspace.setup`, `workspace.unregister`, the automatic `workspace.repair-registry`, and MCP configuration and migration, which record the row a manifest implies |
| `<vault>/_Workspaces/<key>/` | embedded workspace | The folder is the fact: no manifest and no registry row | nothing; it is resolved by its existence |
| `<vault>/Workspaces/*.md` | workspace hub (`living/workspace`) | Vault content. "Workspace registration" means creating or attaching this hub | `workspace.ensure-registration` and `workspace.setup` create or attach it; otherwise the vault's artefact lifecycle |
| `~/.config/brain/brains.json` | derived machine registry (retired) | Nothing reads or writes it. A leftover file is inert and may be deleted once no Brain on this machine runs Brain Core 0.70.10 or earlier, whose `doctor.py`, `doctor_machine.py` and `machine.py` scripts recreate it | nothing |

Where these docs say "registration" without a qualifier, they mean Brain
registration. A row in the vault registry that is no longer its own canonical
path (for example, a symlink left at a moved Brain's old path) is stale and
never selects a Brain; `brain list` and `brain doctor` report each stale row
with its reason and, where one succeeds, its recovery command.

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
- `<vault>/.brain/local/workspaces.json` is the linked workspace registry, derived from the manifests that name this Brain (see [Machine and workspace state](#machine-and-workspace-state))

`workspace.setup` writes the link fields of `.brain/local/workspace.yaml` (`brain` and `links.workspace`) together with `slug`, and `workspace.unregister` removes the two link fields; the rest of the file remains human-editable and is expected to evolve over time.
`workspace.repair-registry` is intentionally narrower: an initially authorised, maintainer-level repair that changes `.brain/local/workspaces.json` only, never the human-owned workspace manifest. It drops a row only when the manifest in the recorded folder names another Brain or workspace, and never adds one. A file that cannot be read is refused with no change. A file whose rows can be read but which holds rows that name no usable folder is rebuilt without them, and its previous content is kept as the one fixed-name backup `.brain/local/workspaces.json.bak`. A file whose rows cannot be read (not UTF-8, not JSON, or the wrong shape) is refused with no change unless the request sets `allow_row_loss: true`, the person's explicit choice to rebuild it empty with the same backup; no extra prompt guards that field. The backup replaces the previous one only after the rebuilt registry has been saved, so a failed repair keeps the earlier backup.

Built-in profile allow-lists that a vault stores in `.brain/config.yaml` are never widened automatically. A grant added to a template profile, such as `workspace.repair-registry` for `maintainer`, reaches new vaults and vaults that keep the template profiles; a vault whose stored profile carries a `label` or other edits keeps it as written until the grant is added by hand.

Canonical `workspace.setup` also ensures a `living/workspace` hub and stores its
bare key in `links.workspace`. That exact link selects the canonical
`workspace/{key}` identity; the slug, registered path and `workspace/*` tags
cannot silently choose a replacement. A configured missing or inactive hub is
reported explicitly by session bootstrap. The manifest's Brain alias must resolve
to the selected vault. Generic metadata updates cannot set `links.workspace`,
and clearing descriptive links preserves it; use composite setup for binding.

Shared workspace policy lives on the hub as optional `default_parent` and
`default_tags`. The local manifest can set `defaults.parent` and additive
`defaults.tags`; it remains outside Brain config merging. Both parent fields
must identify a non-terminal living artefact in the same workspace. Use
`workspace.update-policy` for shared changes and `workspace.update-metadata`
with `parent` or `clear_parent` for the local override. Shared policy fields are
handler-owned: generic creation and frontmatter updates reject them. Workspace hubs are
self-scoped; other membership is explicit `workspace: workspace/{key}` and is
never inferred from tags.

Creation uses explicit parent, then local `defaults.parent`, then shared
`default_parent`, then no parent. Policy tags are additive; local policy applies
only when the effective workspace equals the bound workspace. Artefact document
edits retain their existing membership/parent but restore configured tags.
For ordinary mutations, `workspace_context: global` suppresses these defaults without
reassigning an existing subject. The explicit `artefact.set-workspace` transition
instead uses its context as destination membership, with `global` clearing it.
Even explicit selection fails closed while the
active local binding or its policy is invalid.

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

### Permissions and initial authorisation

The authenticated profile sets the maximum available commands. Initial authorisation is configured separately:

```yaml
vault:
  access:
    request_policy: allowed  # allowed | denied
defaults:
  access:
    initial:
      mode: normal  # normal | read-only | explicit
    overrides: {}
```

`normal` initially authorises ordinary observations and content work; `read-only` authorises observations and their necessary derived caches. `explicit` requires `commands: [exact.command, ...]`, including an empty list when intended. The catalogue owns these classifications. Initial commands are intersected with the authenticated maximum; authenticated control commands remain available independently. `overrides` maps exact command IDs to Booleans, adding or removing initial authorisation without increasing permissions. Attempts to override controls are reported as configuration conflicts.

Shared `vault.access` policy cannot be overridden locally. Each authored `defaults.access.initial` object and the entire `defaults.access.overrides` map replace the lower layer, so an empty map clears inherited overrides and `false` remains meaningful. Other configuration merge rules are unchanged.

`vault.read-config` exposes these effective settings, their template/shared/local sources, the configuration revision and actionable conflicts without credential material. Large inspection results use revision-bound pages. Migration also writes source-specific conflicts to `.brain/local/authorisation-migration.json`, including custom permission profiles missing required controls even when no configuration file needs conversion.

Exceptional authorisation belongs to one Brain, principal and live MCP instance or CLI job. It has no timer and does not survive a new owner instance. `access.prepare` presents an exact operation; `access.request` is the dedicated harness-gatable consent tool. Requests never raise credential permissions. `denied` disables exceptional requests while retaining initial authorisation. Permission administration is the separately authenticated CLI-only `permission.set-profile` operation.

Upgrade preserves stored initial selections as explicit command sets using the pre-upgrade profile definitions. Recognised shipped profiles receive current control commands; custom profiles are retained and conflicts are reported. Former `external` approval becomes the inert `request_policy: migration_required` marker: an administrator must explicitly replace it with `allowed` or `denied`. Historical lease/audit files are never imported as active consent.

### Authentication

The MCP composition root accepts `BRAIN_OPERATOR_KEY` as trusted server configuration; the CLI accepts `--operator-key` as adapter input. Before composing invocation authority, Brain refreshes config, hashes the supplied key with SHA-256 and matches it against registered operators in the vault config. On a match, the invocation uses the operator's configured profile. If no key is supplied, the default profile is used. Operator identity is never a semantic request field.

The default principal has stable identity `default`; its current profile is a separate property. A managed CLI job privately binds its authenticated registration so descendants need not receive the plaintext key. Each invocation re-evaluates current permissions; credential removal or rotation ends that binding. An explicitly supplied different key is refused. Configuration source identity and contents participate in permission generation, so ordinary reduction-and-restore edits invalidate existing grants. Completely unobserved external creation and removal of an optional configuration file are outside that guarantee.

Invocation checks current permissions and applicable authorisation before executor entry. If config is malformed, unreadable, or names an unknown profile/tool, enforcement fails closed. `session.start` supplies the bootstrap; it does not bypass invalid configuration or create compatibility state for removed aggregate tools.

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
once using at most four independent workers, then stages and validates every
configured skill path sequentially within its shared checkout. Results are
reconciled deterministically after the workers finish. One invalid package or
repository therefore cannot hide valid results from another group, while skills
sharing a repository/ref do not incur repeated fetches.

Application-facing Git acquisition accepts only closed HTTPS, SSH URL, or
SCP-style SSH repository grammars. HTTPS URLs cannot contain user information;
SSH identities are restricted to a bounded safe-character grammar. Malformed
authority escapes, local paths, `file://` repositories, query/fragment suffixes,
option-shaped refs and refs containing Git revision expressions are rejected
before Git invocation. A machine-local repository is therefore not readable
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

Claude Code, Codex and Grok discover native skills in global and project directories.
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
.venv/bin/pip install --no-deps -r dependencies/requirements-dev.txt
.venv/bin/pip check
.venv/bin/pytest -q
```

### Test configuration

For dependency updates and optional semantic setup, follow the
[authoritative dependency workflow](../contributor/dependencies.md).

`pyproject.toml` configures pytest with `pythonpath` entries for `src/brain-core` and `src/brain-core/scripts`, so test files can `import check` and `from brain_mcp import server` without `sys.path` manipulation.

## MCP ownership and migration

MCP ownership is not vault configuration. Canonical user claims live in
`<Brain config-home>/brain/mcp-registrations.json`; project/local claims live in
`<vault>/.brain/local/init-state.json`. Both use ledger version 2 and record
schema `brain.mcp-registration/2`. The machine config-home follows XDG (or the
native Windows application-data location). Native client files retain their
own layouts. A claim records client/scope/target and exact last-applied server
and bootstrap evidence; it does not authorise arbitrary stored paths.
`transport_enabled: false` retains ownership of Claude bootstrap projections
needed by another admitted route without expressing intent to reinstall the
removed transport. Brain/workspace repair maintains that bootstrap or removes
it once no surviving route needs it.

The bootstrap lines Brain has written form a closed, versioned set, listed
with their releases in `BOOTSTRAP_LINE_HISTORY`
(`scripts/_bootstrap/mcp_state.py`). One rule brings a file's Brain line to the
current line for its target: the first line in the set is replaced in place,
keeping its indentation and line ending; later ones are dropped; the current
line is appended only when the file holds none; and the rest of the file is
kept byte for byte. MCP configuration, repair and migration, and
`workspace.configure-bootstrap` for `CLAUDE.md` and `AGENTS.md`, all use it.
Removal, including uninstall, removes every line in the set. A claim may record
any line in the set; repair converges the file and the claim on the current
line. Matching is on the whole line, so an edited or bulleted copy is user
text, is never rewritten, and gets the current line added beside it.

TOML transport repair compares parsed command, arguments and environment values.
Equivalent formatting (including an omitted or inline empty environment table)
does not cause a rewrite or a stale MCP registration diagnosis after approval setup.
Unchanged transport preserves the native text; ownership conflicts still fail
before writes.

The migration journal is adjacent to the machine ledger as `mcp-migration.json`.
It contains exact before/after configuration evidence and must be treated with
the same care as the client configuration itself. Normal repair does not parse
legacy ledgers as canonical state. See [MCP lifecycle](cli.md#mcp-registration-and-repair).

## Maintenance state

`<vault>/.brain/local/maintenance/` holds the selected Brain's maintenance
state (DD-082): `pass.lock` (the non-blocking pass lock), `last-pass.json`
(schema `brain.maintenance-pass/1`: pass ID, host, finish time, outcome,
per-family outcomes and counts; a cache for the advisory and for `list`,
never read for correctness), `decisions.json` (schema
`brain.maintenance-decisions/1`: claims and dismissals, the only persistent
maintenance state) and `decisions.lock`. The pass only reads the decisions
file; the decision commands prune and write it under the lock. A dismissal
whose fingerprint no longer matches, or whose finding can no longer be
dismissed (DD-086), is inert until retention prunes it. An unreadable
decisions file blocks the pass, which still writes a blocked summary; move it
aside to recover. The machine pass keeps the same files under
`$XDG_STATE_HOME/brain/maintenance/` (`~/.local/state` by default), beside
the launcher receipts. These are local files, never vault notes, and
`.brain/local` may be synced between hosts, so run passes for one Brain from
one host; the pass warns when the host changes.

## Grok client configuration

The supported client selectors are `claude`, `codex`, `grok` and `all`.
Grok has project and user scope. Explicit Grok local scope is rejected;
`all` with local scope selects only Claude and reports the excluded clients.
Brain's configuration commands target the standard client directories beneath
the supplied home or workspace root. They do not change Grok's authentication,
folder trust, permission policy or model settings.

| Surface | Grok destination |
|---|---|
| Project MCP | `<workspace>/.grok/config.toml` |
| User MCP | `~/.grok/config.toml` |
| Bootstrap rule | `.grok/rules/brain.md` beneath the selected workspace or home |
| Global skill adapter | `~/.grok/skills/<name>/` |
| Project skill adapter | `<workspace>/.grok/skills/<name>/` |

Project MCP registration uses the selected Brain's managed Python and proxy;
user scope uses the stable installed CLI bootstrap, like the other clients.
Both follow the same workspace binding. Native configuration takes precedence over inherited
Claude MCP registrations. Project setup adds only `.grok/config.toml` to Brain's
machine-local ignore entries; the portable startup rule remains discoverable.
Open Grok in the target directory, review its trust prompt and check
`grok inspect` and `grok mcp doctor brain`.

The owned startup rule calls MCP `session_start`, using local
`brain session start --json` as fallback. Grok ignores passive SessionStart hook
stdout, so Brain supplies a rule instead of installing a Claude-style hook.
`workspace.configure-bootstrap` also accepts `{"surface":"grok"}`.

Setup preflights TOML and rule writes together. It preserves unrelated tables
and existing Brain timeout/enabled options. Conflicting rule content, symlinked
state and unsupported TOML layouts fail without overwriting them. Removal
matches the complete recorded Brain server; added options or changed fields
are preserved for review. It removes only the exact authored rule and retains
an ownership record while an edited rule remains, even if the config was
already removed. An inherited Claude MCP registration can become visible again
when the native Grok entry is removed.

MCP migration/configuration/repair preserve Grok's
sibling `[permission]` rules and `[ui]` permission mode, just as Claude's
separate permissions and Codex's tool approval settings remain client-owned.
Brain does not interpret or grant these approvals. Transport changes and
unrecognised server options remain subject to exact ownership checks.

`brain agent-skill configure --request-json '{"client":"grok"}'` installs the
active-Brain shaping adapter. `skill.expose` and `skill.unexpose` accept Grok
for global or project scope and use the existing ownership marker, dry-run and
backup-on-replacement rules. Upgrade repairs existing project MCP registrations;
it does not install new user-global adapters or register an absent client.

These paths describe the standard client home. If Grok is launched with a
custom `GROK_HOME`, its configuration must be provisioned under that home;
Brain does not currently resolve client-specific home overrides.

Client behaviour references: [MCP](https://docs.x.ai/build/features/mcp-servers),
[rules](https://docs.x.ai/build/features/project-rules),
[hooks](https://docs.x.ai/build/features/hooks), and
[skills](https://docs.x.ai/build/features/skills-plugins-marketplaces).
