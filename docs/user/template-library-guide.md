# Template Library Guide

The brain ships with a default set of artefact types — the template library. These are starting points, not requirements. You can modify, remove, or create your own types.

> **Source of truth:** The artefact library at `src/brain-core/artefact-library/` is the canonical reference for type definitions, frontmatter schemas, and naming patterns. This guide describes what ships and when to use each type.

## Living Types

Living types are evergreen artefacts that are maintained and updated over time. They live in named top-level folders in the vault.

### Daily Notes

End-of-day summaries distilled from the day's activity log. The log captures everything in detail; the daily note gives the overview — what tasks got done and what the day amounted to. Use this as a lightweight daily reflection practice, not a second log.

### Designs

Design documents, wireframes, and proposals for features, products, or concepts. Use a design when you want to explore an approach before committing to it. Designs have a full lifecycle from shaping through to implementation or rejection, and serve as the durable record of what was considered and why.

### Documentation

Technical docs, style guides, and prescriptive reference material that governs how work gets done. Use documentation for anything that needs to stay current and actively referenced — standards, procedures, guides. Unlike notes or wiki, documentation has an explicit lifecycle and can be deprecated.

### Ideas

Loose thoughts and concepts articulated to clarity through iterative refinement. Use an idea when something is worth capturing but not yet concrete enough to be a design or project. Ideas can be shaped through Q&A, and when clear enough to act on, they are adopted into a downstream artefact.

### Journals

Named journal streams that group personal journal entries via nested tags. One file per journal stream — the hub for all entries in that stream. Use this type when you want to maintain distinct, named journals (e.g., a personal journal, a work journal) rather than a single undifferentiated stream.

### Notes

A flat, date-prefixed knowledge base for low-friction capture. Use notes when you want to write something down without the overhead of deciding exactly where it fits. Unlike wiki, notes are intentionally flat and require no curation — just write and link.

### People

Person index files — one per person, serving as the living source of truth for what you know about them. Updates replace superseded facts rather than accumulating them. Use people files to anchor everything you know about someone in one place, connecting related artefacts via person tags.

### Projects

Project index files linking to all related artefacts — designs, research, plans, releases, transcripts. Use a project file as the hub for any body of work. All related vault artefacts use a nested project tag so everything connected to a project is findable from one place.

### Releases

Version-scoped shipment records for a project. Use a release artefact when a project has a named version or milestone worth planning, tracking, or recording separately from the project hub. Each release captures the goal, ship gates, human-readable changelog, and sources for one version, and lives under a project-specific folder in `Releases/`.

### Tasks

Persistent units of work — tracked, prioritised, and linked to artefacts. Tasks are brain-native and deliberately minimal: status and wikilinks. Use tasks to track work that has a clear start and end. Works alongside task plugins (Undertask, Linear) for richer UX.

### Wiki

A human-curated knowledge base — one page per concept. Use wiki when you want a polished, comprehensive reference that you maintain deliberately. Unlike notes, wiki requires curation and intent — it's selective. Pages are updated as understanding deepens; duplicates are merged.

### Workspaces

Workspace hub files linking brain artefacts to bounded data containers (`_Workspaces/`). Use a workspace when you have working files (code, spreadsheets, images) that fall outside the vault's artefact taxonomy. The hub gives those files a home in the vault; the data folder holds everything else.

### Writing

Long-form written works with a full lifecycle from draft to published — essays, blog posts, chapters, letters, scripts. Use writing for content that is being crafted for an audience. When status reaches `published`, the file auto-moves to a published subfolder.

### Zettelkasten

An auto-maintained atomic concept mesh — one card per concept, densely linked. Use zettelkasten when you want implicit knowledge structure made explicit across a growing corpus. Cards are discovered automatically from other artefacts and enriched over time. Works well alongside wiki as a complementary fine-grained layer.

---

## Temporal Types

Temporal types are point-in-time records. They are never edited after the fact and live under `_Temporal/` in month-organised subfolders.

### Bug Logs

Point-in-time records of broken behaviour — correctness failures that need resolution. Use a bug log when something is objectively wrong or producing incorrect output, not just harder than it should be. Each bug log has a lifecycle from open to resolved.

### Captures

External material ingested into the vault verbatim — emails, meeting notes, Slack threads, data extracts. Preserved exactly as received and frozen on ingest. Use captures whenever you bring outside content into the vault; downstream artefacts link back to the capture as source material.

### Cookies

A measure of user satisfaction — awarded when work lands well. Use a cookie to mark work that was genuinely good: fast, elegant, surprising, deeply understood. Over time, the cookie log reveals what kinds of work resonate and what approaches are worth repeating.

### Decision Logs

Point-in-time records of decisions, capturing the "why" behind choices. Use a decision log after any significant decision — state the question, list options considered (including rejected ones), and record the reasoning before it fades.

### Friction Logs

Signal accumulator for maintenance. Captures any moment where something generated friction — missing context, conflicting information, inconsistencies, or suboptimal outcomes. Use friction logs as a pattern detector, not a bug tracker. Individual entries are low-cost; value emerges when signals accumulate and point to a systemic issue.

### Idea Logs

Quick idea captures in rough form, with a deliberately low bar for entry. Use an idea log when a new idea strikes and you need to capture it before it slips away. Idea logs can mature: a raw capture becomes a living idea, which becomes a design.

### Ingestions

Processing records for content decomposition. Use an ingestion when breaking down multi-topic content — brain dumps, voice memos, documents with multiple threads — into vault artefacts. The ingestion links to the raw capture and records every artefact created from the source.

### Journal Entries

Personal journal entries — reflections, recollections, and life updates. Use journal entries to record personal experience in your own words. Each entry belongs to a named journal stream via a nested tag. Voice is always the user's; agents preserve phrasing rather than paraphrase.

### Logs

Append-only daily activity logs. Running chronological record of what happened. Use logs as the raw, timestamped record of the day — every meaningful action goes here. Daily notes are distilled from logs at end of day. The filename and month folder are keyed by the log's subject day, so backfilled logs stay on the day they describe even if written later.

### Mockups

Visual or interactive prototypes generated to explore a design direction. Use a mockup when you need to bridge the gap between an abstract design document and real implementation — UI layouts, component designs, app shells. One direction per file.

### Observations

Timestamped facts, impressions, and things noticed. The bar is deliberately low — a single sentence is enough. Use observations to capture discrete facts as they occur: what you learned, what you noticed, what changed. Factual rather than speculative.

### Plans

Pre-work plans written before complex work begins. Records the intended approach, goal, and strategy. Use a plan to align on approach before starting — not a full spec, just enough to know what you're doing and why.

### Presentations

Slide decks generated from markdown content using Marp CLI. The markdown source is the artefact; the PDF is the output. Use presentations to distil vault artefacts — designs, research, reports — into a structured narrative for an audience.

### Reports

Overviews of detailed processes, distilling findings and implications from research, diagnosis, or investigation. Use a report to communicate what a process meant — what was done, what was found, and what should happen next. A report is about what you *did*; research is about what you *learned*.

### Research

In-depth investigation into a subject, capturing what was found. Use research when investigating a topic in depth — comparing approaches, gathering sources, synthesising findings. One topic per file. Research is about what you *learned*; a report is about what you *did*.

### Shaping Transcripts

Q&A refinement transcripts tied to a specific source artefact. Use shaping transcripts to capture the raw Q&A from shaping a design, idea, or other artefact. Each transcript is bound to one source document; if the conversation pivots to a different artefact, start a new transcript.

### Snippets

Short, crafted content pieces derived from existing work — tweets, blurbs, product descriptions, taglines, bios. Use snippets to extract and polish reusable pieces from longer artefacts. Derived from a source; kept tight — a paragraph, a tweet, a tagline.

### Thoughts

Raw, unformed thinking captured in the moment. The bar is deliberately low — if it crosses your mind and feels worth noting, write it down. Most thoughts won't go anywhere, and that's fine. Thoughts are precursors to ideas; ideas are precursors to designs.

### Transcripts

Conversation transcripts — person-to-person, AI conversations, Q&A sessions. Use transcripts to preserve conversations worth keeping. One transcript per conversation; participants are identified consistently and flow is preserved in order.

---

## Choosing the Right Type

Some types are easily confused. Here is guidance on the common decision points.

**Notes vs Wiki vs Zettelkasten** — Three types serve knowledge management. Notes are low-friction flat pages with no curation overhead. Wiki is a deliberately maintained, polished reference — selective and comprehensive. Zettelkasten is an auto-maintained atomic mesh suited to surfacing implicit structure across a growing corpus. Notes are for capturing; wiki is for reference; zettelkasten is for discovery. Wiki and zettelkasten work well as complementary layers in the same vault.

**Thoughts vs Idea Logs vs Ideas** — A thought is the rawest form: unformed, captured in the moment, no obligation to develop it. An idea log is a quick capture with a specific idea worth tracking, with a path toward adoption. A living idea is an articulated concept being shaped to clarity. Capture a thought freely; promote to an idea log or idea when you want to do something with it.

**Research vs Reports** — Research is about what you learned during an investigation: gathered sources, synthesised findings. A report is about what a process produced: findings, implications, next steps. After doing research, you might write a report communicating what it meant.

**Friction Logs vs Bug Logs** — A bug log is for correctness failures: something is objectively wrong or producing incorrect output. A friction log is for quality and experience issues: things that are harder than they should be. If you're unsure, it's probably friction.

**Captures vs Ingestions** — A capture is the raw external material, preserved verbatim. An ingestion is the processing record created when you decompose multi-topic content into vault artefacts. You always create the capture first; an ingestion is only needed when the source has multiple threads worth breaking apart.

**Transcripts vs Shaping Transcripts** — A transcript is a general conversation record. A shaping transcript is specifically a Q&A refinement session tied to a named source artefact. Use shaping transcripts for the structured Q&A that refines an idea, design, or plan; use transcripts for everything else.

**Projects vs Workspaces** — Projects are hubs for vault artefacts connected by a project tag. Workspaces are hubs for working files that fall outside the vault's artefact taxonomy — code, spreadsheets, images. A project can have a workspace; they serve different purposes.
