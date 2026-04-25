# Subfolders Within Living Artefact Folders

Artefacts start as flat files in their type folder. Structure is not planned upfront — it emerges when a single logical work grows to span multiple files. When subfolders appear, they are a projection of canonical ownership, not an informal folder label.

## Organic growth

When a project, book, or other complex work outgrows a single file, a subfolder appears naturally:

- One living artefact becomes the owner
- Living children persist `parent: {type}/{key}` (see [[keys]])
- Same-type child folders use `{key}/`
- Cross-type child folders use `{parent-type}~{key}/`
- The subfolder inherits the child artefact type — no separate taxonomy or CSS needed

Common evolution patterns (e.g. "a book might grow chapters in a subfolder") can be documented in individual artefact taxonomy files as cues, without being prescriptive.

## Master/sub-artefact convention

When a living artefact accumulates enough related sub-artefacts that the type folder gets crowded, the sub-artefacts move into an owner-derived subfolder. The owner stays in the type root as the entry point; the subfolder groups its children.

```
Designs/
  Brain Master Design.md          <- owner stays in root
  brain/                          <- same-type children owned by design/brain
    Brain Inbox.md
    Brain Mcp Server.md
    ...
```

Cross-type ownership uses the owner type prefix:

```
Releases/
  project~brain/
    v0.31.0 - Ownership Contract.md
```

**Rules:**

- The owner artefact stays in its normal location. It is the entry point for that work.
- Same-type children live in `{Type}/{key}/`.
- Cross-type children live in `{Type}/{parent-type}~{key}/`.
- The subfolder name is derived from the canonical owner key, not guessed from the title.
- `brain_create` accepts `parent` as a canonical artefact key, unique name or basename, or relative path. It always persists the canonical `{type}/{key}` value.

**Archiving sub-artefacts:** See the [archiving standard](archiving.md) for how archiving works within ownership-derived subfolders.
