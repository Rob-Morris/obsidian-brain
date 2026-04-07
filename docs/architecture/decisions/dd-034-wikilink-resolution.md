# DD-034: Wikilink resolution strategy

**Status:** Implemented

## Context

Obsidian wikilinks (`[[Target]]`) can reference files by full vault-relative path, by basename, or by a display name that differs from the filename. Agents and humans write links in varied forms — sometimes using slugs, sometimes using human titles, sometimes omitting folder prefixes. The system must resolve these to actual files on disk for reading, editing, and link-update operations.

Obsidian itself resolves by basename (shortest unambiguous match). Brain-core needs compatible resolution that also handles artefact naming conventions: temporal files use a `yyyymmdd-{prefix}~{Title}` pattern, older files may use `--` as the title separator, and links may carry trailing backslashes from Windows paths.

## Decision

Two resolution entry points serve different call sites:

**`resolve_artefact_path(name, vault_root)`** — Used when a tool is given a path or basename for an artefact. Builds a case-insensitive basename index of the vault, then does a direct lookup. Accepts names with or without `.md` extension and with or without folder prefixes. Returns a single match or raises on ambiguity/not-found.

**`resolve_broken_link(target, file_index)`** — Used when a wikilink target does not match any file by exact basename. Applies a cascade of repair strategies in order:

0. Trailing backslash cleanup (Windows paste artifacts)
1. Tilde-space normalisation (`~ ` → `~`, fed into later strategies)
2. `slug_to_title()` — convert hyphenated slug to title-cased basename
3. Double-dash to tilde — `{prefix}--{slug}` → `{prefix}~{Title}` (legacy separator migration)
4. Dated slug + temporal prefix — reconstruct `{date}-{prefix}~{Title}` for temporal artefacts
5. Path stripping — strip folder prefix and re-run all strategies on basename
6. Path segment title-casing — title-case the last path segment

Each strategy is tried in order; the first single-match result wins. Ambiguous matches are reported as such so the caller can ask for disambiguation rather than silently picking one.

**`build_wikilink_pattern(*stems)`** — Builds a compiled regex matching all Obsidian wikilink forms (plain, anchored, block-referenced, aliased, embedded). Longer stems are tried first so full-path stems are preferred over basename-only stems.

## Consequences

- Agents can reference artefacts by casual name (title, slug, basename) without knowing the exact vault path.
- The multi-strategy cascade handles both legacy `--` separators and current `~` separators transparently.
- Ambiguous links surface an error rather than silently resolving to the wrong file.
- `_Archive/` is excluded from resolution indexes — archived files are found only via explicit archive paths.
