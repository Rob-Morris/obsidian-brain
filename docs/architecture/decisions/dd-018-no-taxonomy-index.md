# DD-018: Taxonomy Index Dropped — Filesystem is the Index

**Status:** Implemented (v0.4.0)

## Context

Early iterations included a taxonomy index file listing all artefact types with their keys and paths. This required updating the index whenever a type was added, renamed, or removed — another thing to keep in sync, another place for drift to occur.

## Decision

No taxonomy index file. The filesystem is the index. Types are discovered by scanning `_Config/Taxonomy/` and vault folders (DD-016). The type key derivation convention (lowercase folder name, spaces to hyphens) means any tool can compute the path to a taxonomy file from the type key without a lookup table.

## Consequences

- No index to maintain or synchronise when types change.
- Tools that need the full type list scan the filesystem — this is fast at vault scale.
- The discovery convention must be documented and consistently followed; it is not encoded in a config file.
- Agents reading the vault without tools get the same index for free by listing `_Config/Taxonomy/` — no special file needed.
