# Getting Started with Brain

## What is the Brain For?

The Brain remembers for you so you don't have to. More than that, it remembers in a way that it understands what you mean and can help you do the things you want to do.

Most note-taking systems start organised and slowly decay. Files pile up, naming drifts, folders become dumping grounds, and finding things depends on remembering where you put them. AI agents make this worse — they create files fast but have no memory of what's already there, so they duplicate, misfile, and fragment your knowledge.

Brain solves this by giving your vault a self-reinforcing structure. Every file has a typed home. Naming and frontmatter follow predictable conventions per type. Agents can find existing work before creating new work, file things in the right place without being told, and maintain vault integrity as they go. Because the structure is consistent and machine-readable, agents don't just store your knowledge — they understand it well enough to surface the right context when you need it, connect related ideas across your vault, and act on your behalf with real awareness of what you've already thought, decided, and built.

The vault gets more useful over time, not less. You spend less time organising and more time thinking. You capture ideas without worrying about where they go. You come back after a break and find things where you expect them. Your agents work with the same conventions you do, so their output fits seamlessly alongside yours.

---

## Installation

Optional [managed Brain approvals](../functional/approvals.md) are selected
separately from MCP installation. Choose clients, scope and MCP/CLI surfaces
explicitly; omission leaves your approval policy unmanaged.

The quickest way to create a new Brain vault:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/rob-morris/obsidian-brain/main/install.sh)
```

Or from a local clone of the repo:

```bash
bash install.sh /path/to/brain
```

On native Windows, use the PowerShell launcher from a local clone:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -VaultPath C:\path\to\brain
```

`install.cmd` is a cmd.exe wrapper over the same PowerShell launcher.

For non-interactive agent installs in restricted environments, scaffold the vault without MCP setup:

```bash
bash install.sh --non-interactive --skip-mcp /path/to/brain
```

The installer creates the vault from the template, copies `.brain-core/` into it, provisions the light machine resolution runtime used by no-MCP `brain session`, and then asks for explicit client selection (Claude Code, Codex, Grok, or All supported clients) and a registration scope — register this Brain for this vault only (project scope, the default) or as your machine default brain (user scope) — provisioning the managed Python runtime as needed. `install.sh` and `install.ps1` both hand fresh/existing-vault install policy to the shared Python installer core at `src/brain-core/scripts/install.py`. The POSIX wrapper can also install brain-core into an existing Obsidian vault and detect already-installed Brain vaults; for those, the canonical upgrade path is `upgrade.py` and `install.sh` only delegates to it. In network-restricted environments you can pass `--skip-mcp` to scaffold the vault without runtime / MCP setup, or rerun the printed retry steps later if dependency installation fails. Use `--non-interactive --client all` (or name one client) for automated MCP setup; it selects the this-vault-only (project) scope. When upgrade changes either shipped runtime dependency export (`requirements.txt` or `requirements-semantic.txt` under `.brain-core/brain_mcp/`), `upgrade.py` provisions the matching shared runtime under `~/.brain/venvs/` itself; `install.sh --skip-mcp` passes through the opt-out. Same-version re-apply, downgrade, or explicit migration rerun flows remain explicit `upgrade.py --force` operations. Project scope still outranks user scope for all three clients once the project-scoped MCP is active: in Claude, approve `brain` via `/mcp`; in Codex, trust the project and ensure `brain` is enabled for that project; in Grok, review its folder-trust prompt. See [install.sh](../functional/scripts.md#installsh) and [install.py](../functional/scripts.md#installpy) for full details, modes, and flags.

Semantic retrieval remains optional. Enable it later with
`brain retrieval enable --vault /path/to/brain --json`. That command
installs the pinned semantic runtime into the central managed runtime, snapshots
the pinned local model under `.brain/local/semantic-models/`, records
`.brain/local/semantic-model-manifest.json`, and refreshes embeddings sidecars
so semantic search stays local-only at query time.

**Requirements:** git and Python 3.12+. Python 3.12+ is required for install, init, upgrade, repair, and MCP server support because the shell launchers hand scaffold policy to the Python installer core. Brain installs its managed dependencies into a shared local runtime under `~/.brain/venvs/` rather than into your wider Python environment.

Base and optional semantic dependencies use complete locked exports. Changes to
either shipped export select a new shared runtime, even when semantic search is
disabled; optional packages remain opt-in. Explicit runtime repair verifies
installed versions as well as dependency consistency. Users need pip, not uv.
Native release certification covers CPython 3.12 on macOS arm64, Linux x86_64 and
Windows x86_64. Other Python 3.12+ combinations are best-effort. Current semantic
wheels require macOS 14+ arm64 or glibc 2.28+ Linux, and ONNX Runtime has no Intel
macOS wheel. Dependency or model download failures are reported separately
from the usable vault scaffold.

## Command-line usage

The installed `brain` CLI uses the same noun/verb grammar as MCP, direct script and typed Python projections:

```bash
brain vault check --request-json '{"actionable":true}' --json
brain runtime repair --json
brain command describe artefact.create --json
```

For selected-Brain automation without the machine-global CLI, use the one direct projection:

```bash
python3 .brain-core/scripts/command.py vault check \
  --request-json '{"actionable":true}' --json
```

### Agent bootstrap

MCP `session.start` (the `session_start` tool) is the normal bootstrap path. Follow `range.next_cursor` with another call until `bootstrap_complete` is true before ordinary work. If a source revision changes, restart without a cursor.

When MCP is unavailable, the launcher alternative is:

```bash
brain session start --json
```

Run it in the vault or a bound external workspace, or select a vault with `--vault /path/to/brain`. The launcher resolves the selected Brain and invokes the same canonical `session.start` owner. Complete its bootstrap pages too. Supported direct scripts are another tool-backed route; `command.py` can invoke `session start` when its interpreter meets that command's managed-runtime requirements.

The generated `.brain/local/session.md` is a Markdown mirror of the canonical session bootstrap. It is derived from the same model, not an independently authored source of truth. An agent without usable MCP, CLI or scripts can read that mirror through `.brain-core/index.md` if it exists.

If those tools and generated assets are unavailable, follow `.brain-core/md-bootstrap.md`. It routes through shipped `.brain-core/` instructions, `_Config/router.md` and the relevant taxonomy. This explicit fallback requires no code execution or compilation; the copied core instructions and authored vault files are enough.

Commands declare bootstrap, portable or managed dependency tiers. The adapter never silently provisions or changes tier; availability and one next action are part of the structural result. See [User Reference](user-reference.md#dependency-and-availability-model) and [Script Reference](../functional/scripts.md).

When you already have a registered Brain and want to register and bind a folder without choosing transport policy, use `workspace.setup`:

```bash
brain workspace setup --vault /path/to/brain --workspace /path/to/project \
  --request-json '{}' --json
```

That ensures a canonical Brain workspace hub first, then creates or repairs
`.brain/local/workspace.yaml` with `brain + slug + links.workspace` and adds
Brain-owned machine-local ignore rules in git-backed targets. Registration and
local binding are reported separately: if the second step fails, inspect the
known partial result and retry. Existing content is not adopted from tags; use
the [explicit adoption workflow](workflows.md#explicit-adoption-and-reassignment)
before selecting an existing project as the shared default parent. Setup does not
write `.mcp.json`, `.codex/config.toml`, or SessionStart hooks. Configure transport
later only if you want it, for example:

```bash
# Project scope for one workspace and all three clients
brain mcp configure --vault /path/to/brain --workspace /path/to/project \
  --request-json '{"scope":"project","client":"all"}' --json

# User scope everywhere on the machine
brain mcp configure --vault /path/to/brain \
  --request-json '{"scope":"user","client":"all"}' --json
```

To converge the selected Brain's bootstrap scaffold after binding, run the separate application command:

```bash
brain workspace configure-bootstrap --vault /path/to/brain \
  --workspace /absolute/path/to/project --json
```

This local CLI command writes only the workspace's
`.brain/local/workspace.yaml`; it does not create a Brain project or workspace
artefact.

To make any active Brain workflow discoverable as a native skill in Claude Code
and Codex, expose a thin discovery adapter explicitly:

```bash
brain skill expose --vault /path/to/brain \
  --request-json '{"name":"shaping","client":"all","scope":"global"}' --json
```

The adapter contains no workflow of its own. It calls `session_start`, resolves
the unqualified effective skill user-first, and loads that package from the
active Brain. A normal Brain or skill update therefore changes the workflow
without copying it into each client directory. Existing unmanaged skills are
preserved; after reviewing one, use `"replace":true` to archive its old
directory outside skill discovery under
`~/.<client>/.brain-skill-backups/` and install the adapter. Restart Claude Code
and Codex after the command reports a change. Installation is explicit because
these are machine-global client
directories, not vault-owned files.

Use `"scope":"project"` with `--workspace /path/to/project` for project-local
discovery. An existing canonical workspace binding takes precedence; otherwise
the configured machine default is used. The exposure command will not create or
change a binding.

`vault.read-file` is limited to ordinary non-hidden vault files and explicit public Brain Core documentation trees such as `.brain-core/skills/`. It cannot read `.brain/`, `.brain/local/`, `.obsidian/`, Brain Core defaults/scripts or a symlink resolving into those private namespaces.

---

## Two Kinds of Things

Everything in the Brain is either **living** or **temporal**. This is the only distinction you need to understand up front.

**Living artefacts** are things that evolve. A wiki page about Rust lifetimes, a design doc for your new app, an essay you're drafting. You come back to them, update them, and the current version is what matters. They live in root-level folders like `Wiki/`, `Designs/`, or `Writing/`.

**Temporal artefacts** are snapshots. A log of what you did today, a transcript of a conversation, research notes from investigating a problem. They capture a moment and then they're done. They live under `_Temporal/`, in a folder per type.

The relationship between them is where the Brain gets interesting. Temporal artefacts feed living ones. You jot down an idea in an idea log; later it becomes a living idea; later still it becomes a design. A research session produces temporal research notes; the findings end up in a wiki page. The Brain tracks these connections so nothing gets lost in translation.

---

## It's Just Markdown

There's no database, no proprietary format, no app you have to use. Your Brain vault is a folder of markdown files on your computer. Every artefact, every configuration file, every piece of the system — plain text, readable in any editor.

The Brain itself (`.brain-core/`) is a set of markdown docs and Python scripts that ship inside your vault. The configuration (`_Config/`) is more markdown — taxonomy definitions, templates, style guides, your personal preferences. The scripts compile these into a JSON file that tools can read quickly, but the source of truth is always the markdown you can open and edit.

This means you can work with your vault directly in Obsidian. Open files, edit them, use Obsidian's graph view to see connections, search with Obsidian's built-in search. The Brain's conventions (consistent naming, typed frontmatter (YAML metadata at the top of each file), wikilinks (`[[double-bracket links]]` between files) in the body) are designed to make Obsidian's features work well — backlinks resolve cleanly, graph view shows meaningful structure, and Dataview queries can filter by type or status.

When you work with an AI agent, session bootstrap supplies instructions and discovery routes derived from these files. The router and taxonomy are configuration inputs; the agent uses the tools to find relevant artefacts and read the type definitions it needs. Agents using the authored Markdown fallback read those inputs directly. You can also create and edit files in Obsidian: the conventions are simple enough to follow by hand.

The tools exist to make things faster, not to make things possible.

---

## What's in the Vault

Here's what a well-used Brain vault looks like at a glance:

```
Wiki/                         ← polished knowledge base
Zettelkasten/                 ← atomic concept cards (auto-maintained)
People/                       ← person hubs
Projects/                     ← project indexes
Releases/                     ← versioned release records
Workspaces/                   ← workspace hubs (linked to _Workspaces/ data)
Designs/                      ← design docs and proposals
Ideas/                        ← concepts being explored
Journals/                     ← named journal streams
Tasks/                        ← persistent units of work
Writing/                      ← essays, posts, chapters
Documentation/                ← technical docs and style guides
Notes/                        ← low-friction knowledge notes
Daily Notes/                  ← end-of-day summaries

_Temporal/
  Logs/                       ← daily activity timeline
  Plans/                      ← pre-work strategy
  Research/                   ← investigation notes
  Transcripts/                ← conversation records
  Shaping Transcripts/         ← Q&A refinement sessions
  Idea Logs/                  ← raw idea captures
  Journal Entries/             ← personal reflections
  Thoughts/                   ← unformed thinking
  Decision Logs/              ← "why we chose X"
  Friction Logs/              ← "this generated friction"
  Reports/                    ← process overviews
  Snippets/                   ← crafted short-form content
  Cookies/                    ← "that earned a cookie"
  Observations/               ← timestamped facts and things noticed
  Mockups/                    ← visual/interactive prototypes
  Captures/                   ← ingested external material
  Ingestions/                  ← processing records for content decomposition
  Presentations/              ← slide decks (Marp)

_Workspaces/                  ← freeform data containers for workspaces
_Assets/                      ← images, PDFs, generated output
_Config/                      ← router, taxonomy, styles, memories, preferences
_Plugins/                     ← external integrations
.brain-core/                  ← the Brain system itself
```

You won't have all of these on day one. Types get added as you need them. The vault grows with you.

---

## Your Vault, Your Way

### Starting Small

A new Brain vault ships with a practical starter set: Daily Notes, Designs, Documentation, Ideas, Notes, People, Projects, Releases, Tasks, Workspaces, Writing (living); Bug Logs, Captures, Cookies, Decision Logs, Friction Logs, Ingestions, Logs, Mockups, Observations, Plans, Presentations, Reports, Research, Shaping Transcripts, Snippets, Thoughts, Transcripts (temporal). That covers the core workflows — capturing knowledge, designing and documenting, tracking people and projects, planning and shipping releases, managing tasks, managing workspaces, writing, logging activity, recording decisions and observations, ingesting external material, refining artefacts, logging friction, capturing raw thinking, prototyping designs, presenting work, tracking bugs, and rewarding good work. You can add more types from the library as you need them.

### Adding Types When You Need Them

When you find yourself creating content that doesn't fit anywhere, that's the signal to add a type. The artefact library (`.brain-core/artefact-library/`) has ready-to-install definitions for types like Wiki, Journals, Zettelkasten, Printables, and more. Each comes with a taxonomy file and template. Folder colours are regenerated by `runtime.refresh-router`.

Use `brain type status --request-json '{}' --json` to inspect library types, then
install or update one with `brain type sync --request-json
'{"type_key":"living/wiki"}' --json`. Definition sync also runs automatically
after upgrades according to the vault's `artefact_sync` preference. It also
updates unmanaged custom taxonomies when a filing convention changes — today
that means rewriting a `## Naming` folder ending in `yyyy-mm` to file flat. An
upgrade dry run previews the rewrite before it is applied; `artefact_sync:
skip` suppresses it, and Naming folders that don't match are preserved and
warned about. See
[Direct Script and Python Command Interfaces](../functional/scripts.md) and use
`brain command describe type.sync --json` for the exact request contract.

The rule of thumb: add a type when you'll create multiple files of that kind and they need different conventions from what you already have. If it's a one-off, a subfolder or tag within an existing type is simpler.

### Growing Organically

Artefacts start as single files. When something outgrows one file, structure emerges naturally. Your novel starts as `Writing/my-novel.md` and eventually becomes `Writing/my-novel/index.md` with chapter files alongside. No upfront planning needed — the Brain adapts as your content grows.

A common pattern is **master/sub-artefacts**: when a master artefact accumulates enough related files to crowd the type folder, sub-artefacts move into a named subfolder while the master stays in the type root as the entry point.

```
Designs/
  Brain Master Design.md          ← master stays in root
  Brain/                          ← sub-artefacts cluster here
    Brain Inbox.md
    Brain Mcp Server.md
```

This works for any living type — designs, ideas, wiki pages. Sub-artefacts inherit the parent type, so no separate taxonomy or CSS is needed. When a sub-artefact reaches a terminal status, use `artefact.set-status`; the handler moves it into the matching `+Status/` folder. Use `_Archive/` only for deliberate removal from the active vault namespace.

### Giving Agents Context with Memories

When you mention a project, tool, or concept and your agent doesn't know what you're talking about, it can look it up. Memories (`_Config/Memories/`) are reference cards — factual context that agents load on demand.

Each memory has triggers (words or phrases you'd naturally use) and a body (what the thing is, where to find it, key facts). When you say "brain core" and the agent lacks context, it finds the memory with that trigger and reads it.

You can create memories for anything agents should know about — your projects, your tools, your codebase conventions. They're simple markdown files with a `triggers` list in frontmatter. Memories are "what is it?" context — if the agent needs "how do I do it?" steps, that's a skill. See the [Reference](user-reference.md) for the full format.

### Telling the Brain About You

Two files in `_Config/User/` shape how agents work with your vault:

- **`preferences-always.md`** — your standing instructions. How you like to work, what quality standards matter, what agents should always do (or never do).
- **`gotchas.md`** — learned pitfalls. Things that went wrong before and shouldn't happen again. Agents read this every session.

These are freeform. Write whatever helps.

---

## Upgrading

To upgrade brain-core to a new version:

- **CLI**: `brain upgrade --vault /path/to/brain --request-json '{}' --json` (add `"force": true` for same-version re-apply, downgrade, or migration rerun)
- **install.sh wrapper**: `bash install.sh /path/to/brain` — detects the existing install and delegates to `upgrade.py`

The checked upgrade runs every pending versioned migration in order, completes
the selected Brain's runtime warm-up before returning success, and records the
result in `.brain/local/last-upgrade.json`. If readiness cannot complete, the
launcher returns a known partial outcome with `brain runtime warmup` and
`brain runtime status` recovery guidance. A failed MCP ownership migration likewise
leaves the committed Core/CLI upgrade in place and reports recovery work;
direct `upgrade.py` reports `status: partial` and exits 1. Preserve client
approval settings when resolving registration conflicts; they are client policy,
not permission for Brain to replace transport ownership.

When upgrading from before 0.70.3, an optional [managed approvals](../functional/approvals.md)
follow-up points to `brain approvals inspect --json`. Review it, then explicitly
choose client, scope and surfaces with `brain approvals configure` if wanted.
The notice neither enables approvals nor removes manually created rules.

Upgrade never silently deletes shared machine runtimes;
when read-only topology inspection proves orphan candidates, it reports `brain
runtime remove-orphans --dry-run` and the explicit removal command. When the Claude/Codex/Grok shaping discovery
adapter is first introduced or its template changes, it recommends
`configure.py agent-skills --client all` but does not run it automatically.
Ordinary updates to the active Brain's shaping workflow produce no adapter
prompt because installed adapters load that workflow dynamically.

An open MCP session refreshes Core automatically only when its managed runtime
is unchanged. Dependency changes require an MCP restart: agents receive a clear
`runtime_restart_required` result, while already-running work can finish. The
agent can use `brain_proxy_restart` when idle on supported POSIX systems; if that
fails or is unsupported, restart MCP in the host. CLI commands resolve the
current runtime on each invocation and do not need that session restart.

---

## Checking and Repairing

If you are not sure what is broken, start with:

```bash
brain vault check --request-json '{"actionable":true}' --json
```

When `vault.check` detects shaped infrastructure drift, it returns the exact
granular repair command to run.

Repair one named scope at a time:

```bash
brain runtime repair --json
brain mcp repair --json
brain runtime refresh-router --request-json '{"force":true}' --json
brain retrieval refresh-lexical --request-json '{"force":true}' --json
brain workspace repair-registry --json
brain retrieval repair-semantic --json
```

New MCP setup requires an explicit client or `all` (All supported clients).
For unattended installation, pass `--client all` alongside `--non-interactive`;
use `--skip-mcp` for a scaffold only. Windows uses `-Client all`.

For most broken-tooling cases, `brain runtime repair` is the important command when
the shared managed runtime under `~/.brain/venvs/` is broken. `mcp.repair`
repairs recorded caller-workspace project MCP state against that usable runtime.
Use `--request-json '{"scope":"user"}'` for the shared user connection,
`'{"breadth":"brain"}'` for a selected Brain's runtime and all registered
integrations, or `'{"breadth":"machine"}'` for all registered local Brains.
None acts as a first-time installer or adds an unselected client. Existing
pre-0.70 registrations first need `brain mcp migrate --json`; inspect with
`--dry-run`. See [migration and bootstrap recovery](../functional/cli.md#migration-and-bootstrap-recovery)
for missing base Python, conflicts and interrupted transitions.
`retrieval.repair-semantic` is the semantic equivalent after a
vault has been opted in with `retrieval.enable`. It
restores the pinned runtime packages, local model snapshot/manifest, and
embeddings sidecars together. `router`, `lexical`, and `registry` are narrower
generated-state repairs and are usually best run when `vault.check` tells you to.

The CLI's launcher recovery stays bootstrap-safe and converges packageful work
into the central managed runtime under `~/.brain/venvs/`; it does not install
packages into your wider Python environment. Use `brain vault check`,
`brain runtime refresh-router`, `brain retrieval refresh-lexical`,
`brain workspace repair-registry` and `brain retrieval repair-semantic` for
the narrower generated-state scopes.

---

## Going Deeper

- **[System Guide](system-guide.md)** — architecture, conventions, and how the Brain works under the hood
- **[Template Library Guide](template-library-guide.md)** — the artefact library, installing types, and extending the system
- **[Workflows](workflows.md)** — day-to-day usage patterns: logging, ideas, knowledge building, working with agents
- **[Configuration](../functional/config.md)** — operator profiles, privilege levels, and vault configuration
- **[MCP Tools](../functional/mcp-tools.md)** — the agent tools: search, create, edit, and vault operations

## Using Grok

Choose `grok` wherever Brain asks for a client, or `all` to configure Claude,
Codex and Grok together. Grok-only setup writes native configuration and a
startup rule; it does not require Claude to be installed. Open Grok in the
vault or bound workspace, review its folder-trust prompt, then run
`grok inspect` and `grok mcp doctor brain` to check discovery and connectivity.
Ask Grok to call `session_start` and check the returned vault and workspace.
See [native client setup](../functional/cli.md#native-grok-setup).
