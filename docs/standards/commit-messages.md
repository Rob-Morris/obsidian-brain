# Commit Messages

How to write good commit messages in this repo. Applies to every contributor, human and agent.

A good commit message does three things: makes the subject scannable in `git log`, explains the *why* in prose so a reader does not have to reconstruct it from the diff, and names specific identifiers so future readers can grep.

## Process

Before drafting, read four things:

1. **`git diff` and `git diff --stat`** — know what actually changed.
2. **The matching `docs/CHANGELOG.md` index row** and **the corresponding `docs/changelog/vX.Y.Z.md` entry's top-line Summary**, if this change ships a version. Both carry the same canonical Summary text — one short scannable sentence describing the version's user-observable effect. The commit subject reuses that Summary verbatim with a `(vX.Y.Z)` suffix.
3. **The body of the corresponding `docs/changelog/vX.Y.Z.md` entry**, if one exists. Per-version file bodies are written as prose; paraphrase the commit-message body from there instead of reinventing the narrative.
4. **`git log --oneline -15`** — match the local subject-line style.

Skip steps 2 and 3 only for non-versioned support commits that do not ship a version, for example `docs:`, `test:`, or `chore:` work.

## Subject Line

Versioned template: `<Summary> (vX.Y.Z)`

Non-versioned template: `<prefix> <specific subject>`, where `<prefix>` is `docs:`, `test:`, or `chore:`

- **Short** — under about 70 characters.
- **Specific** — name the new thing by its identifier, not by category. `safe_write_via kernel` beats `shared atomic write kernel`; the former is greppable, the latter is not.
- **Versioned** — if the change bumps `src/brain-core/VERSION`, include the new version in parens at the end. Always parenthesised, never `as vX.Y.Z`.
- **No period** at the end.
- **Imperative mood** — "Harden embeddings writes", not "Hardened" or "Hardens".

For release commits the subject is `<Summary> (vX.Y.Z)`, where `<Summary>` is the canonical Summary text — the top-line Summary of the per-version file, also filled into the matching `docs/CHANGELOG.md` index row. Do not re-paraphrase: the per-version Summary is the source of record, drafted before commit; the index cell carries the same text; the subject reuses it verbatim. See [Changelog](changelog.md).

For non-versioned support commits, prefixes are required and narrow by design:

- `docs:` — documentation-only work
- `test:` — test-only work
- `chore:` — repo-only maintenance that does not ship a version

Do not use prefixes on versioned commits. In this repo, shipped code, fixes, features, and any change that bumps `src/brain-core/VERSION` already carry stronger release structure via semver, changelog entries, and the canonical Summary subject. Keep the prefix set small; add new prefixes only if a real recurring non-versioned category appears. Until then, every non-versioned commit should use exactly one of the prefixes above, and the subject after the prefix should still be short, specific, and imperative.

Good:

```text
Harden embeddings writes via safe_write_via kernel (v0.29.6)
Key temporal logs off subject day; fail closed on rename collisions (v0.29.4)
Frontmatter-backed filename rendering (v0.29.0)
docs: clarify AGENTS.local.md local-override scope
test: cover partial file-index basename upgrades
```

Avoid:

```text
Update build_index.py                  # what changed? why?
Harden embeddings                      # harden how?
Refactor atomic writes (v0.29.6)       # "refactor" hides intent
docs update                            # prefix present, but still vague
```

## Body

The body explains *why* the change exists — the motivation, the mechanism, the downstream effect. It is optional: trivial commits skip it. Two parts when used, in order:

**1. Explanation** — concept-level bullets by default; prose only when a change has a strong causal arc that bullets would fragment.

Operating principle: **less words, less bullets, still clear.** Add bullets only when the diff doesn't already explain why.

- Hard cap: 120 characters per bullet, 6 bullets per body. No exceptions.
- One focus per bullet: a single contract, behaviour, trade-off, or invariant.
- Each bullet adds a fact a reviewer cannot get from the subject or the diff. If it doesn't, drop it.
- Explain conceptually — never enumerate *where in the codebase*; the diff shows that.
- Reference specific identifiers (function names, flag names, type names) inline only when they add meaning.
- If your change won't fit in 6 bullets at 120 characters, the commit is doing too much — split it.

When prose is the right choice, the same content rules apply. Prefer 2–4 sentences; cap at 6.

**2. Reference** — a single trailing line pointing to a public-safe reference that motivated the work, if one exists. Public-safe means a stranger can verify the reference using only `git log` and the public web — nothing requiring access to a private system, machine, or workspace.

Examples of valid references include repo paths, semver versions, public URLs, and named public standards. The form is `Label: value` on a single trailing line:

```text
Issue: #123
```

```text
DD: docs/architecture/decisions/dd-036-safe-write-pattern.md
```

## Worked Example

```text
Harden embeddings writes via safe_write_via kernel (v0.29.6)

Embeddings persistence was the last code path still calling np.save()
directly, bypassing the atomic-write guarantees the rest of the
pipeline already had. Introduce safe_write_via() as a callback-driven
kernel that routes any serializer through one sibling-tempfile + fsync
+ os.replace path, then move text, JSON, and the embeddings arrays
onto it via thin serializer-specific wrappers. This collapses three
parallel atomic-write implementations into one, makes adding a new
serializer a one-wrapper change, and closes the last gap in
build-time crash safety.

DD: docs/architecture/decisions/dd-036-safe-write-pattern.md
```

## Rules

**Words must earn their place.** Every sentence and bullet must add a fact a reviewer cannot get from the subject or the diff. If it doesn't, drop it. Length is not a quality signal; signal density is.

**Why, not what.** The diff shows what. The message explains why. If your message could be reconstructed by reading the diff, it is not earning its keep.

**Specific identifiers over category nouns.** Name the function, file, or flag by its actual identifier. A reader searching `git log` for `safe_write_via` should find this commit; a reader searching for `atomic write kernel` should also find it, but the specific identifier is the one that cannot be confused with anything else.

**Versioned commits stay prefix-free.** If the change ships a version, use the canonical Summary subject with the `(vX.Y.Z)` suffix, not `fix:`, `feat:`, `refactor:`, or `chore:`.

**One logical change per commit.** If a single commit message needs two paragraphs to describe two unrelated changes, split the commit. The exception is coordinated changes that only make sense together, for example renaming a function and updating every call site.

**Never combine unrelated changes** even when staging is convenient. Logical coherence over commit count.

**Match recent `git log` style.** Subject conventions drift over time; always check the last 10–20 commits before writing.

## When The Rules Do Not Apply

Trivial commits do not need a body:

```text
docs: fix typo in docs/user/user-reference.md
chore: bump dev dependency pytest 8.3 -> 8.4
docs: clarify AGENTS.local.md naming
```

If there is genuinely nothing to explain beyond the subject, do not invent filler. But most commits in this repo are not trivial; when in doubt, write the body.

## Related

- [Changelog](changelog.md) — tiered public release-history standard
- [Agent Workflow](agent-workflow.md) — contributor workflow tiers
