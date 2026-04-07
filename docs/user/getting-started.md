# Getting Started with Brain

## What is the Brain For?

The Brain remembers for you so you don't have to. More than that, it remembers in a way that it understands what you mean and can help you do the things you want to do.

Most note-taking systems start organised and slowly decay. Files pile up, naming drifts, folders become dumping grounds, and finding things depends on remembering where you put them. AI agents make this worse — they create files fast but have no memory of what's already there, so they duplicate, misfile, and fragment your knowledge.

Brain solves this by giving your vault a self-reinforcing structure. Every file has a typed home. Naming and frontmatter follow predictable conventions per type. Agents can find existing work before creating new work, file things in the right place without being told, and maintain vault integrity as they go. Because the structure is consistent and machine-readable, agents don't just store your knowledge — they understand it well enough to surface the right context when you need it, connect related ideas across your vault, and act on your behalf with real awareness of what you've already thought, decided, and built.

The vault gets more useful over time, not less. You spend less time organising and more time thinking. You capture ideas without worrying about where they go. You come back after a break and find things where you expect them. Your agents work with the same conventions you do, so their output fits seamlessly alongside yours.

---

## Installation

The quickest way to create a new Brain vault:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/robmorris/obsidian-brain/main/install.sh)
```

Or from a local clone of the repo:

```bash
bash install.sh ~/brain
```

The installer creates the vault from the template, copies `.brain-core/` into it, sets up a Python virtual environment, and registers the MCP server for Claude Code. It also handles upgrades (re-run on an existing vault) and installing into an existing Obsidian vault. See [install.sh](../functional/scripts.md#installsh) for full details, modes, and flags.

**Requirements:** git and python3. Python 3.10+ is recommended for MCP server support — without it the vault is still created but agent tools won't be available until you install Python and run `init.py` manually.

---

## Two Kinds of Things

Everything in the Brain is either **living** or **temporal**. This is the only distinction you need to understand up front.

**Living artefacts** are things that evolve. A wiki page about Rust lifetimes, a design doc for your new app, an essay you're drafting. You come back to them, update them, and the current version is what matters. They live in root-level folders like `Wiki/`, `Designs/`, or `Writing/`.

**Temporal artefacts** are snapshots. A log of what you did today, a transcript of a conversation, research notes from investigating a problem. They capture a moment and then they're done. They live under `_Temporal/` in monthly folders.

The relationship between them is where the Brain gets interesting. Temporal artefacts feed living ones. You jot down an idea in an idea log; later it becomes a living idea; later still it becomes a design. A research session produces temporal research notes; the findings end up in a wiki page. The Brain tracks these connections so nothing gets lost in translation.

---

## It's Just Markdown

There's no database, no proprietary format, no app you have to use. Your Brain vault is a folder of markdown files on your computer. Every artefact, every configuration file, every piece of the system — plain text, readable in any editor.

The Brain itself (`.brain-core/`) is a set of markdown docs and Python scripts that ship inside your vault. The configuration (`_Config/`) is more markdown — taxonomy definitions, templates, style guides, your personal preferences. The scripts compile these into a JSON file that tools can read quickly, but the source of truth is always the markdown you can open and edit.

This means you can work with your vault directly in Obsidian. Open files, edit them, use Obsidian's graph view to see connections, search with Obsidian's built-in search. The Brain's conventions (consistent naming, typed frontmatter, wikilinks in the body) are designed to make Obsidian's features work well — backlinks resolve cleanly, graph view shows meaningful structure, and Dataview queries can filter by type or status.

When you work with an AI agent, it uses the same files. The agent reads your vault's router and taxonomy to understand the conventions, uses search tools to find relevant artefacts, and creates files that follow the same patterns you'd use yourself. But none of this requires the agent. You can create and edit files directly in Obsidian, and the structure holds because the conventions are simple enough to follow by hand.

The tools exist to make things faster, not to make things possible.

---

## What's in the Vault

Here's what a well-used Brain vault looks like at a glance:

```
Wiki/                         ← polished knowledge base
Zettelkasten/                 ← atomic concept cards (auto-maintained)
People/                       ← person hubs
Projects/                     ← project indexes
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

A new Brain vault ships with a practical starter set: Daily Notes, Designs, Documentation, Ideas, Notes, People, Projects, Tasks, Workspaces, Writing (living); Captures, Cookies, Decision Logs, Friction Logs, Logs, Observations, Plans, Reports, Research, Shaping Transcripts, Snippets, Thoughts, Transcripts (temporal). That covers the core workflows — capturing knowledge, designing and documenting, tracking people and projects, managing tasks, managing workspaces, writing, logging activity, recording decisions and observations, ingesting external material, refining artefacts, logging friction, capturing raw thinking, and rewarding good work. You can add more types from the library as you need them.

### Adding Types When You Need Them

When you find yourself creating content that doesn't fit anywhere, that's the signal to add a type. The artefact library (`.brain-core/artefact-library/`) has ready-to-install definitions for types like Wiki, Journals, Zettelkasten, and more. Each comes with a taxonomy file and template. Folder colours are auto-generated when you run `brain_action("compile")`.

To install types from the library, use `brain_action("sync_definitions")` (or `python3 sync_definitions.py` from the CLI). You can preview with a dry run, sync specific types, or let it run automatically after upgrades. See [sync_definitions](../functional/scripts.md) and [brain_action](../functional/mcp-tools.md) for full parameters.

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

This works for any living type — designs, ideas, wiki pages. Sub-artefacts inherit the parent type, so no separate taxonomy or CSS is needed. When a sub-artefact reaches a terminal status, use `brain_action("archive")` to move it to the top-level `_Archive/` (preserving type/project structure). Projects archive as-is.

### Giving Agents Context with Memories

When you mention a project, tool, or concept and your agent doesn't know what you're talking about, it can look it up. Memories (`_Config/Memories/`) are reference cards — factual context that agents load on demand.

Each memory has triggers (words or phrases you'd naturally use) and a body (what the thing is, where to find it, key facts). When you say "brain core" and the agent lacks context, it finds the memory with that trigger and reads it.

You can create memories for anything agents should know about — your projects, your tools, your codebase conventions. They're simple markdown files with a `triggers` list in frontmatter. Memories are "what is it?" context — if the agent needs "how do I do it?" steps, that's a skill. See the [Reference](../user-reference.md) for the full format.

### Telling the Brain About You

Two files in `_Config/User/` shape how agents work with your vault:

- **`preferences-always.md`** — your standing instructions. How you like to work, what quality standards matter, what agents should always do (or never do).
- **`gotchas.md`** — learned pitfalls. Things that went wrong before and shouldn't happen again. Agents read this every session.

These are freeform. Write whatever helps.

---

## Upgrading

To upgrade brain-core to a new version:

- **Re-run install.sh**: `bash install.sh ~/brain` — detects the existing install and offers to upgrade
- **CLI**: `python3 upgrade.py --source /path/to/src/brain-core` (from the repo, not the vault)
- **Manual**: replace `.brain-core/` with the new version from `src/brain-core/`

---

## Going Deeper

- **[System Guide](system-guide.md)** — architecture, conventions, and how the Brain works under the hood
- **[Template Library Guide](template-library-guide.md)** — the artefact library, installing types, and extending the system
- **[Workflows](workflows.md)** — day-to-day usage patterns: logging, ideas, knowledge building, working with agents
- **[Configuration](../functional/config.md)** — operator profiles, privilege levels, and vault configuration
- **[MCP Tools](../functional/mcp-tools.md)** — the agent tools: search, create, edit, and vault operations
