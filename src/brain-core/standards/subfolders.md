# Subfolders Within Living Artefact Folders

Artefacts start as flat files in their type folder. Structure is not planned upfront — it emerges when a single logical work grows to span multiple files.

## Organic growth

When a project, book, or other complex work outgrows a single file, a subfolder appears naturally:

- One file in the subfolder acts as the **index** (`index.md` or the project slug, e.g. `my-book.md`)
- The subfolder inherits the parent artefact type — no separate taxonomy or CSS needed
- CSS prefix selectors (e.g. `[data-path^="Writing/"]`) handle nested content automatically

Common evolution patterns (e.g. "a book might grow chapters in a subfolder") can be documented in individual artefact taxonomy files as cues, without being prescriptive.

## Master/sub-artefact convention

When a master artefact accumulates enough related sub-artefacts that the type folder gets crowded, the sub-artefacts move into a named subfolder. The master stays in the type root as the entry point; the subfolder groups its children.

```
Designs/
  Brain Master Design.md          <- master stays in root
  Brain/                          <- sub-artefacts cluster here
    Brain Inbox.md
    Brain Mcp Server.md
    ...
```

This is a general convention for any living artefact type — designs, ideas, wiki pages, etc.

**Rules:**

- The master artefact stays in the type root. It is the entry point for the project.
- Sub-artefacts live in `{Type}/{Project}/` where `{Project}` is the subfolder name.
- Sub-artefacts inherit the parent artefact type — no separate taxonomy needed.
- The subfolder name is a short project label (e.g. `Brain`, `Pistols`), not the full master title.
- `brain_create` accepts an optional `parent` parameter to place new artefacts directly into a project subfolder.

**Archiving sub-artefacts:** See the [archiving standard](archiving.md) for how archiving works within project subfolders.
