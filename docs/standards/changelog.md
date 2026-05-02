# Changelog

How repo release history is documented for install managers and contributors.

## Package

The live changelog is a tiered package:

- `docs/CHANGELOG.md` — newest-first index of shipped versions
- `docs/changelog/vX.Y.Z.md` — one file per shipped version
- `docs/changelog/releases/vX.Y.Z-<slug>.md` — one file per shipped milestone release

The index stays at `docs/CHANGELOG.md` because the repo already uses that conventional filename, and `docs/changelog.md` would collide with it on case-insensitive filesystems.

## Update Flow

When shipping a new version:

1. Create `docs/changelog/vX.Y.Z.md`.
2. Add the new row to `docs/CHANGELOG.md`.
3. If the version closes a shipped `living/release`, add or update the matching file under `docs/changelog/releases/`.
4. Never rewrite older per-version files to “fix history”. Add corrections in the current version instead.

## Per-Version Files

Each `docs/changelog/vX.Y.Z.md` file documents one shipped version.

Required shape:

- top-line `Summary`: one short bold sentence (~60–75 chars), user-observable effect first. The Summary is the canonical short text for the version — see §Index for how it relates to the index `Summary` cell and the release commit subject.
- optional supporting context bullets
- detail bullets — every detail bullet names ≥1 specific identifier (file path, function or method, type name, folder, config key, MCP tool, or test file). Verification claims like "tests added" or "schema validated" must name the test or schema file. Vague bullets ("code refactored", "tests added", "docs updated") are not permitted; the reader should be able to navigate from any detail bullet directly to the change it describes.

Rules:

- Use `**BREAKING — ...**` in the Summary when install-manager action is required.
- Put migration steps under a `Migration:` context group when needed.
- Keep non-public references out of public changelog files (see Public References below).

Stacked sub-entries: a per-version file usually documents one themed change — one VERSION bump, one top-line Summary, supporting bullets below it. When a version legitimately bundles multiple unrelated changes (e.g., release-prep housekeeping consolidating several small fixes), each change appears as a sub-entry below the top-line Summary, each with its own bold sub-summary and bullets. Optional `###` thematic sub-headings may group related sub-entries. The top-line Summary still frames the whole file and is the canonical text that fills the index `Summary` cell. The `BREAKING —` marker stays on the affected sub-entry's bold sub-summary — top-line framing does not substitute for per-sub-entry signals.

## Index

`docs/CHANGELOG.md` is the scannable entry point.

- One row per shipped version
- Newest first
- `Version` links to `docs/changelog/vX.Y.Z.md`
- Ordinary version rows carry a `Summary` — one short scannable sentence (~60–75 characters, imperative mood, no period, no version suffix, names a specific identifier) describing the user-observable effect of the version
- The Summary is one canonical text used three places: the per-version file's top-line Summary, the index `Summary` cell, and the release commit subject (with a `(vX.Y.Z)` suffix appended). Drafted once into the per-version file and the index cell as the same text, before commit; reused as the commit subject. See [Commit Messages](commit-messages.md).
- Preserve a `BREAKING —` prefix in the `Summary` when the per-version entry requires install-manager action
- Release rows use `Release: <title>` in the `Summary` column and link that title to the per-release file

## Per-Release Files

Release files narrate a shipped milestone across a contiguous version range.

Expected sections:

- `## Overview`
- `## Breaking changes` when needed
- `## Highlights`
- `## Upgrade notes` when needed
- `## Versions in this release`

These files are generated or backfilled from shipped `living/release` artefacts and the version history; they are not replacements for the per-version files.

## Public References

Public changelog files may include any reference a stranger can verify using only the file itself and the public web — nothing requiring access to a private system, machine, or workspace.

Examples of valid references include repo paths, semver versions, GitHub tags, GitHub URLs, and external URLs.

## Related

- [Commit Messages](commit-messages.md) — per-change engineering narrative for `git log`
- [Canary](canary.md) — contributor review checklist pattern
