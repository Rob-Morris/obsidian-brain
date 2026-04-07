# DD-037: Generous filename matching

**Status:** Implemented

## Context

Artefact filenames are human-readable: they preserve the title the user or agent provides. Obsidian supports Unicode filenames natively, and users work across macOS, Windows, and Linux, each with different sets of forbidden filename characters.

Two competing concerns arise:

1. **Machine slugs** (used for hub tags, internal IDs) must be ASCII-safe and URL-safe: `[a-z0-9-]+`.
2. **Human filenames** (used for `.md` files in the vault) should be as close to the original title as possible to remain readable in Obsidian and in filesystem explorers.

Aggressively normalising filenames to slugs (e.g., stripping all non-ASCII) produces ugly, hard-to-read file names and destroys information in titles that contain meaningful punctuation or Unicode characters.

## Decision

Two functions serve the two use cases:

**`title_to_slug(title)`** — Used for hub tags and internal identifiers. Applies NFKD Unicode normalisation, strips to ASCII, lowercases, and replaces non-alphanumeric runs with hyphens. Output is strictly `[a-z0-9]+(?:-[a-z0-9]+)*`. Used for `project/{slug}` and `workspace/{slug}` tags.

**`title_to_filename(title)`** — Used for vault `.md` filenames. Described in its docstring as "generous": it preserves spaces, capitalisation, and Unicode. Only characters that are unsafe across all three major operating systems are stripped: `/ \ : * ? " < > |`. Whitespace is normalised (collapse multiple spaces, trim edges). The result is as close to the original title as possible while being safely writable to disk on any OS.

**`slug_to_title(slug)`** — Best-effort reverse of `title_to_slug()` for wikilink resolution. Replaces hyphens with spaces and title-cases each word. Used by `resolve_broken_link()` when an agent references a file by its slug form.

## Consequences

- Vault filenames remain human-readable in Obsidian and filesystem explorers, even for titles with accented characters, emoji, or punctuation.
- Machine slugs are consistent, URL-safe, and ASCII-only for use in frontmatter tags and external systems.
- `slug_to_title()` is approximate — it cannot recover the original capitalisation or punctuation from a slug. This is acceptable because it is used only as a fallback resolution strategy, not as a round-trip transformation.
- The character blocklist (`_UNSAFE_FILENAME_RE`) is cross-platform, not OS-specific, so files created on one platform are safe on all others.
