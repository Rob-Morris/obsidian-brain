# Hub Pattern

Some living artefact types act as hubs — containers that group related artefacts via nested tags. The pattern:

1. The hub file is a living artefact with a nested tag: `{type}/{slug}` (e.g. `project/my-app`, `journal/personal`)
2. All related artefacts (temporal or living) carry the same nested tag
3. The hub is the index; the tag is the query mechanism

This is useful when a stream of related work or content needs a single living touchpoint. The hub file describes the stream and links to key artefacts; the tag makes everything findable.

**Current examples:**
- **People** — `person/{slug}` groups observations and other artefacts related to a person
- **Projects** — `project/{slug}` groups plans, research, designs, logs, and other artefacts related to a project
- **Journals** — `journal/{slug}` groups journal entries belonging to a named journal stream
- **Workspaces** — `workspace/{slug}` groups brain artefacts related to a bounded working container (`_Workspaces/`). The workspace hub connects brain content to a freeform data folder of non-artefact files

**When to use:** When you need a living artefact that organises a collection of other artefacts (especially temporal ones) rather than containing content itself. If the living artefact is primarily content (like a wiki page or design doc), tags alone suffice — the hub pattern adds an explicit index file.
