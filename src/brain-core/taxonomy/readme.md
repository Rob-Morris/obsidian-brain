# Taxonomy

Artefact type definitions live in `_Config/Taxonomy/`, split by classification:
- `_Config/Taxonomy/Living/` — types that evolve over time
- `_Config/Taxonomy/Temporal/` — types bound to a moment

Each file defines one artefact type: purpose, folder, naming, frontmatter.
To find the right type, list the directory matching your classification.

## Type keys

Derive the key from the folder name: lowercase, spaces to hyphens.
e.g. `Daily Notes` → `_Config/Taxonomy/{classification}/daily-notes.md`
