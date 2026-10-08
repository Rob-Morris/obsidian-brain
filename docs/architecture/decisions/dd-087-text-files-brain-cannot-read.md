# DD-087: Text files Brain cannot read

**Status:** Accepted
**Extends:** [DD-082](dd-082-check-driven-maintenance.md), [DD-086](dd-086-dismissals-need-something-to-reopen-them.md)

## Context

Brain expects strict UTF-8, but many per-file checks silently skip undecodable
files. Other readers let a codec error abort a check, router compilation or
search rebuild. A file can disappear from checks and search without anyone
being told why. The broken-link crash fixed in `f227dce6` exposed this gap;
the original bytes were not retained, so their encoding is unknown.

A leading UTF-8 byte-order mark caused a separate correctness problem: it hid
frontmatter and made YAML config keys differ from their intended names. The
config could silently fall back to template access settings. Parser tolerance
was delivered independently in `ab5906bd`; reporting and removing the mark
must not be prerequisites for correct parsing.

[DD-082](dd-082-check-driven-maintenance.md) makes detection authoritative and
separates unattended derived repairs from repairs requiring judgement.
[DD-086](dd-086-dismissals-need-something-to-reopen-them.md) defines the identity
a dismissal may rely on. This decision extends those rules to text files,
distinguishing deterministic repairs from conditions whose cause needs investigation.

The rules below cover diagnosis, reporting, explicit repair, named refusals,
builder skipping and text-write enforcement. Parser tolerance remains
independent of reporting and repair.

## Decision

### D1. Clear damage is a closed list

Four codes have deterministic fixes whose output the classifier accepts:

| Code | Detection | Fix | Lossless |
|---|---|---|---|
| `utf32_bom` | UTF-32 byte-order mark, strict decoding, no U+0000 | Re-encode as UTF-8 and remove every leading U+FEFF | yes |
| `utf16_bom` | UTF-16 byte-order mark, excluding UTF-32, strict decoding, no U+0000 | Re-encode as UTF-8 and remove every leading U+FEFF | yes |
| `utf8_bom` | UTF-8 byte-order mark; after every leading mark, strict UTF-8 with no U+0000 | Remove every leading mark | yes |
| `truncated_utf8` | No NUL byte; strict decoding fails only with `unexpected end of data` at the end, with a valid multibyte character before the cut | Drop the incomplete final character, 1–3 bytes | no |

Fixed bytes are strict UTF-8, contain no U+0000, have no leading U+FEFF and
diagnose as `None`. Fixes are idempotent, including when the source has a
second leading mark. Line endings and everything else are preserved byte for
byte. Combined damage is not a clear case. Conditions needing a guess,
including legacy 8-bit encodings and UTF-16 without a mark, are reported by
`unreadable_file` instead.

### D2. Truncation requires prior multibyte evidence

A valid multibyte character before the cut is strong evidence of UTF-8, not
proof. Windows-1252 text containing “Ã©” (`C3 A9`) and ending in “Ã” (`C3`)
also matches. The repair therefore requires judgement. Its preview shows the
dropped bytes and their Windows-1252 reading, so the person can recognise a
misdiagnosis. It warns that content after the cut may already be lost: the
repair only makes the remaining file readable.

### D3. Use two checks

`text_encoding` reports clear cases with a repair family. `unreadable_file`
reports ambiguous cases without one. Their identities and remedies differ.
This is a domain distinction, not a repair-table limitation: families attach
per finding and `JUDGEMENT_FINDINGS` is keyed by `(check, code)`.

### D4. Put the classification rule in one pure module

`diagnose_text(data: bytes) -> TextDiagnosis | None` belongs in
`src/brain-core/scripts/_common/_text_encoding.py`, with no I/O. It checks
UTF-32 marks before UTF-16 marks, then a UTF-8 mark, then any NUL byte, then
strict UTF-8. UTF-32 comes first because `FF FE 00 00` begins with `FF FE`.
NUL precedes decoding so unmarked UTF-16 with non-ASCII text is `not_text`.

The frozen `TextDiagnosis` exposes `code`, `lossless`, `fixed_bytes`,
`dropped_bytes`, `line` and `column`: fixed bytes for clear cases, dropped
bytes for truncation, and the first invalid byte's one-based line and byte
column for `not_utf8`. Code constants and
`strip_leading_byte_order_marks(text: str) -> str` live beside it. The helper
removes every leading U+FEFF and nothing else. Later checks, repair, router
and config errors, document decoding and body-file admission use this one rule.
I/O owners produce `os_error`; the byte classifier cannot encounter an `OSError`.

### D5. Give text repair its own command

`vault.repair-text` is a new command rather than an `artefact.repair` scope:
its set includes `_Config` and bootstrap files, which are not artefacts.
It has maintainer authority, the lowest authority that can write `_Config`,
`CONTENT` initial authorisation, and the same surfaces as `artefact.repair`.
Its judgement family is `text_encoding`, with `recovery=False`.

The request has one optional field, `paths`. Omission repairs all current
clear findings; a supplied subset repairs only those files and refuses a
path with no current finding. This lets a person leave out a suspect
truncation while repairing the rest. The dry run lists paths, codes and byte
counts, including truncation evidence and instructions for omitting a file
or converting it manually.

Planning and applying need no router: a UTF-16 taxonomy may prevent it from
compiling. The owner uses the existing lock-plan-admit-apply primitives,
re-diagnoses files under the vault mutation lock, records digests and binds
the plan at admission. Its write allow-list is the shared scanned set rather
than the artefact-only write guard. Successful apply reconciles the router
and transition indexes. Effects are recorded per rewritten path; a write
failure gives `Partial`, names the failed file and retains the other effects.

### D6. Classify the repair as judgement

Existing automatic families write derived state and run as observation-class
commands, including in read-only vaults. Automatic content rewriting would
introduce a new policy. This decision keeps text repair on request, as D17
requires.

### D7. Identity follows the condition

`not_utf8` and `not_text` use `SUBJECT`. The path and code identify what the
person chose to keep; hashing its content would reopen the dismissal on
every edit. A different code reopens it as a different key. `os_error` uses
`EVIDENCE` with the errno name, for example `{"errno": "EACCES"}`, or the
exception class name when errno is absent. A different read failure can then
reopen the dismissal. All three gain literal-code producer rows in
`JUDGEMENT_FINDINGS` under DD-086's scan contract.

### D8. Severity reflects whether Brain can use the file

Every `unreadable_file` code, `utf16_bom`, `utf32_bom` and `truncated_utf8` is
an error: the file is omitted or misread by checks, search and link resolution.
`utf8_bom` is a warning because parser tolerance already preserves its meaning;
removing it tidies remaining cosmetic effects, such as a marked heading in
a note without frontmatter.

### D9. Do not invent a parser-failure category

The Markdown frontmatter parser cannot raise. Config parse failures remain
config errors. A reader failing on bytes the classifier accepts is a Brain
bug and surfaces with a traceback, rather than being mislabelled file damage.

### D10. Repair bytes with compare-then-replace

Immediately before each atomic byte write, re-read the file and compare its
digest with the plan. A changed file is `skipped: changed`. Byte writes keep
line endings; vault bounds and no-symlink writes protect the allowed set.
The comparison narrows the race with external editors but does not close it.

### D11. Do not create backup copies

Lossless fixes have an exact inverse. The lossy truncation is previewed for
a person before apply. No backup mechanism is added.

### D12. Tolerate marks in parsers as well as offering removal

YAML accepts one leading U+FEFF through `load_yaml_text`, with file loading
routed through it. Frontmatter entry points recognise one leading mark
before `---`; the router's memory-trigger reader shares that parsing rule.
This fixes config fallback and hidden frontmatter at their parsing boundaries
rather than changing roughly one hundred readers. Serialisation drops the
mark naturally. The warning and repair remain useful without carrying the
correctness burden. This tolerance was delivered in `ab5906bd`.

### D13. Builders skip; mutations convert or refuse

Lexical build and update, resource-body and semantic embedding builders skip
undecodable sources; the router key index already does so. Other files stay
searchable and `unreadable_file` reports the omission. Refusing a rebuild
would leave all search stale and fail the automatic lexical repair repeatedly.
Builders never convert source files: doing so would be unattended content repair.

Mutations convert lossless cases with a reported conversion and refuse the
rest by name. Skipping an unreadable backlink holder could leave broken
links. `workspace_scan_unreadable` still stops its scan, since skipping could
give children false ownership findings, but its guidance points to the
per-file finding. Dismissal does not silence that ownership failure; a
deliberately kept non-text file belongs in `_Assets`.

### D14. Detect text problems before the router gate

The text checks depend only on the filesystem and run before the router gate,
even when the router is absent. A router compile decode failure is a typed,
definite, no-effect error naming the path and diagnosis, rather than an
unknown outcome. Clear codes point to `vault.repair-text`; other codes point
to `vault.check`. Maintenance can then name the cause of blocked compilation.

### D15. Scan the text Brain reads

One lifecycle-owned `iter_vault_text_files(vault_root)` is shared by checking
and repair. It includes artefact Markdown under non-dot, non-underscore
top-level folders plus `_Temporal` and `_Archive`; Markdown in `_Config`
except managed skill packages; Markdown in `_Plugins`; and root bootstrap
files defined by `ROOT_BOOTSTRAP_VARIANTS`, including their local overrides.
It excludes other underscore folders such as `_Assets` and `_Workspaces`,
dot folders, derived JSON and config YAML. Managed skills belong to their
source and `skill.status`; `skill.update` restores damaged packages.

Only regular files identified with `lstat` are scanned; symlinks and files
that vanish before reading are skipped. Scanning unrelated files could force
cloud downloads and flag content no Brain reader touches. Repairing managed
skills would create local modifications that block their source update.
`CheckContext` reads each scanned file once, caches the diagnosis and reuses
decoded text for document checks. Read errors other than disappearance become
`os_error`.

### D16. Report legacy encodings without converting them yet

`not_utf8` reports the first invalid byte's line and column and advises
conversion to UTF-8 in an editor. `not_text` identifies binary content or
damaged or unmarked UTF-16/UTF-32. A deliberately retained non-note in the
scanned set may be moved to `_Assets`; an outside-set read refusal gives
only the diagnosis. `os_error` advises checking ownership, permissions and
whether an online-only cloud file needs downloading or a connection.

Known legacy-encoding detection and conversion are a separate follow-up,
with manual conversion retained for unrecognised encodings. That future
repair can attach to the same `not_utf8` code, finding and identity. A guided
repair requiring the person to name the encoding is not an interim feature.

### D17. Lossless repairs also remain on request

Maintenance lists the judgement family and its command, never runs it
unattended. Automatic conversion would be the pass's first edit to authored
notes, config definitions and bootstrap files, require revisiting DD-082's
admission test and read-only authorisation, and risk racing synced edits on
another device. Such files are rare; parser tolerance already makes a mark
harmless. A UTF-16 taxonomy can block the router until a person runs the
named router-independent repair. Any later unattended content policy needs
its own decision considering all candidate repairs together.

### D18. Vault text is UTF-8 without a byte-order mark

Brain has no multi-encoding reader. No need was identified for preserving
another encoding in the scanned set: Brain, Obsidian, git and agent tools
handle UTF-8. Legitimate exceptions, such as Windows registry exports and
some PowerShell scripts, belong outside it in `_Assets`. Broad UTF-16/UTF-32
support would require routing about 148 read calls across 79 modules and
defining every writer's encoding rule while keeping files non-standard.

### D19. Normalise Brain writes and entry points

Writers already use UTF-8. Remove every leading U+FEFF once at entry:
inline content in `decode_mutation_content` (MCP and CLI `--body`), staged
content in `stage_body` before storage, and local body files in
`resolve_body_file`, including `stage.py --body-file`. Prepared pins and
`body_sha256` then cover normalised text. Body files use the classifier to
convert the lossless marked encodings and refuse other non-standard bytes
with a named path and code.

The text-mode vault write primitive raises `ValueError` for a leading
U+FEFF: reaching it is a broken invariant. Bootstrap and no-frontmatter
append readers must first use the converting document seam so their
existing marks cannot reach this guard.

### D20. Detect external files and convert during edits

Checks detect files arriving outside Brain, and repair fixes clear cases on
request. An edit provides defence in depth: a person is touching the file,
conversion is lossless and its result reports the conversion. Document-seam
reads with a warning channel return converted text for the read-then-edit
flow. Other readers, including search and checks, stay strict and keep
reporting the source until it is converted. Maintenance and builders never
convert. Collateral files convert too: renaming A may convert backlink holder
B, and the result names every converted path.

### D21. The document seam is strict by default

`decode_persisted_document` preserves strict UTF-8 without a leading mark
exactly as before, including U+0000. Such bytes comply with the encoding
standard, although the check classifies NUL as probably not text. Other
bytes go to the classifier and, by default, raise
`NonStandardVaultTextError(ValueError)` naming path, code and remedy. It is
not a `UnicodeDecodeError` subclass, so tolerant codec handlers cannot
silently swallow it.

`convert_lossless=True` returns fixed text and records the code in
`PersistedDocumentContent`. Only callers with a warning channel or a write
opt in: `artefact.read`, `vault.read-file`, `resource.read`, `open_document`
and write paths. Templates, type definitions and router collections remain
strict. Truncation and ambiguous cases are refused even with the opt-in;
truncation points to the previewed repair. Revisions cover raw disk bytes.
Existing CRLF-to-LF document normalisation remains, so an edit's encoding
conversion is lossless in text, unlike the repair's byte-preserving conversion.
Ordinary reads incur no new classification or caching.

### D22. Keep existing command versions

Named refusals replace raw codec failures and uses existing
`FOLLOW_UP_REQUIRED` warnings and `CONFLICT` refusals. Warnings name the path
and classifier code; refusals name the reason, paths and next action. No
previously successful operation becomes a failure. Removing a leading mark
from an opted-in read fixes text whose parser meaning was already preserved.
These changes do not meet the breaking-change version rule.
`vault.check` remains version 3; the new `vault.repair-text` starts at version 1.

### D23. Refuse vault-wide rewrites with every blocking path

A file that cannot be converted blocks any rewrite whose planner scans the
vault for backlinks: renames, moves, key changes and reparents. The planner
cannot know whether unreadable content references the target. Collect every
blocking file and list them in one refusal, including uninspected repair
paths, so one check and one round of corrections can clear them together.
CLI rename and fix-links paths must honour unreadable rewrite plans too.
This enforces the text standard; deliberately retained non-text belongs
in `_Assets`.

## Alternatives Considered

- Guessing legacy encodings now: deferred. Windows-1252, Latin-1 and Mac Roman
  accept almost arbitrary bytes, so decodability alone does not establish intent.
- Combining both checks: technically possible, but obscures the domain split
  between a known repair and investigation.
- Broad multi-encoding reads or conversion at every reader: rejected in favour
  of one classifier, parser tolerance and explicit document-seam opt-in.
- Automatic lossless conversion: declined under D17; unattended authored
  content changes need a separate policy decision.
- Skipping an unreadable file when its bytes contain no `[[`: unsafe for
  unmarked UTF-16, where the sequence is `[\0[\0`, and only covers some cases.
- Skipping unreadable backlink holders with a warning: rejected because it can
  silently leave broken links.
- Backup copies and an interim encoding-selection command: not added under
  D11 and D16.

## Consequences

The checks name every non-conforming file in the scanned set without
making router availability a prerequisite. Composed readers must also survive
decode failures: router and lexical caches become stale with reason
`unreadable`, JSON and TOML readers use their existing parse/read failure paths,
and MCP bootstrap inspection gets a distinct `unreadable` reason rather than
falsely reporting missing bootstrap text.

Config YAML remains outside the scanned set because undecodable config prevents
all commands, including check and repair, from running. Loading raises a
`ConfigError` naming `.brain/config.yaml` or `.brain/local/config.yaml`, the
diagnosis and manual conversion remedy. A recovery-script route is out of scope.

Search can stay fresh for readable sources, while mutations cannot silently
lose backlinks. Dismissal preserves a deliberate per-file choice but does
not remove an ownership-scan failure. Built-in profiles project the new
command from maintainer authority; stored customised profiles need it added
explicitly and are never widened automatically.

Restoring marked config's intended keys restores its intended access settings
and requires release documentation. Double-encoded but valid UTF-8 and Unicode
normalisation are outside scope. Atomic replacement retains the existing
Brain behaviour of resetting file mode and creation time.

## Implementation Notes

The stable classifier precedes its I/O consumers. Dependency-ordered slices add
named boundary errors, the shared scan and ambiguous findings, builder skips,
then the clear check, repair family and command together. Builders only start
skipping once per-file findings exist. Entry normalisation and document-seam
conversion precede the final write guard.

Every write path must use the opt-in seam or refuse by name: conversion,
archive/unarchive, rename, link rewriting, transition preparation, workspace
transitions and policy/integrity updates, shaping start, frontmatter repair,
ownership scanning, render plans, key-reference indexing and bootstrap
`FileTransaction` reads. Audit callers' exception handling rather than
widening codec catches.

Stage 1 verification covers all classifier codes and fixes, repeated marks,
idempotence, combined damage, the Windows-1252 counter-example, unmarked
non-ASCII UTF-16, UTF-32 precedence, CRLF preservation and the strip helper.
Integration verification covers scan exclusions and one-read caching, real finding
producers and identity pins, check survival with missing router and local MCP
state, definite config/router errors, subset previews and partial repair,
changed-file skips, and router-independent repair of a marked taxonomy.

A catalogue-driven sweep exercises every mutation's target and collateral
roles as UTF-16 and truncated UTF-8: each converts with every path reported or
refuses by name. Raw codec errors, `internal_error` and unknown outcomes fail
the sweep. Multi-file refusal, raw-byte revisions, prepared normalised pins,
strict U+0000 decoding, the write guard and a seeded Brain Lab scenario
complete the implementation evidence. The scenario checks config access
settings, clear repair, retained ambiguous bytes and fresh search results.
