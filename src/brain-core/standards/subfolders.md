# Subfolders Within Living Artefact Folders

Artefacts start as flat files in their type folder. Structure is not planned upfront — it emerges when a single logical work grows to span multiple files.

When a project, book, or other complex work outgrows a single file, a subfolder appears naturally:

- One file in the subfolder acts as the **index** (`index.md` or the project slug, e.g. `my-book.md`)
- The subfolder inherits the parent artefact type — no separate taxonomy or CSS needed
- CSS prefix selectors (e.g. `[data-path^="Writing/"]`) handle nested content automatically

This is organic growth, not upfront architecture. Common evolution patterns (e.g. "a book might grow chapters in a subfolder") can be documented in individual artefact taxonomy files as cues, without being prescriptive.
