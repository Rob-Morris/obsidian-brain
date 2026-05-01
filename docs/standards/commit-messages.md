# Commit Messages

How to write good commit messages in this repo. Applies to every contributor, human and agent.

A good commit message does three things: makes the subject scannable in `git log`, explains the *why* in prose so a reader does not have to reconstruct it from the diff, and names specific identifiers so future readers can grep.

## Process

Before drafting, read three things:

1. **`git diff` and `git diff --stat`** — know what actually changed.
2. **The corresponding `docs/CHANGELOG.md` entry**, if one exists for this change. Changelog entries in this repo are deliberately written as prose; paraphrase tighter for the commit message instead of reinventing the narrative.
3. **`git log --oneline -15`** — match the local subject-line style.

Skip step 2 only if the change is too small for a changelog entry, for example a test-only fix.

## Subject Line

Template: `<verb> <specific noun> via <specific mechanism> (vX.Y.Z)`

- **Short** — under about 70 characters.
- **Specific** — name the new thing by its identifier, not by category. `safe_write_via kernel` beats `shared atomic write kernel`; the former is greppable, the latter is not.
- **Versioned** — if the change bumps `src/brain-core/VERSION`, include the new version in parens at the end.
- **No period** at the end.
- **Imperative mood** — "Harden embeddings writes", not "Hardened" or "Hardens".

Good:

```text
Harden embeddings writes via safe_write_via kernel (v0.29.6)
Key temporal logs off subject day; fail closed on rename collisions (v0.29.4)
Frontmatter-backed filename rendering (v0.29.0)
```

Avoid:

```text
Update build_index.py                  # what changed? why?
Harden embeddings                      # harden how?
Refactor atomic writes (v0.29.6)       # "refactor" hides intent
```

## Body

Three parts, in order:

**1. Prose paragraph** — motivation → mechanism → consequence. Explain *why* the change exists, what new mechanism it introduces, and what downstream effect that has. Use concrete identifiers such as function names, file paths, and flag names. One paragraph, typically 3–6 sentences.

**2. File-level bullets** — each bullet starts with the path or module and says what changed in that file specifically. This is not a restatement of the diff; it is a summary keyed to where a reviewer would look.

**3. Reference** — a single trailing line pointing to the plan, issue, or DD that motivated the work, if one exists:

```text
Plan: 20260419-plan~Embeddings Atomic Write Hardening.md
```

or

```text
DD: docs/architecture/decisions/dd-036-safe-write-pattern.md
```

## Worked Example

```text
Harden embeddings writes via safe_write_via kernel (v0.29.6)

Introduce safe_write_via() in src/brain-core/scripts/_common/_filesystem.py
as a low-level callback-driven atomic-write primitive, and route
build_index.py's type-embeddings.npy and doc-embeddings.npy persistence
through it via a local _safe_save_npy() wrapper, eliminating the last
direct np.save(path, ...) calls for embeddings outputs.
safe_write_json() now shares the same kernel, so text, JSON, and
handle-driven serializers all go through one sibling-tempfile + fsync +
os.replace path.

- src/brain-core/scripts/_common/_filesystem.py: expose safe_write_via();
  route safe_write() and safe_write_json() through the shared kernel.
- src/brain-core/scripts/build_index.py: persist both embeddings arrays
  via _safe_save_npy() backed by safe_write_via().
- tests/test_common_filesystem.py: add regressions for success,
  cleanup-on-failure, bounds refusal, and text-mode.
- tests/test_build_index.py: integration test proving build_embeddings()
  routes both .npy writes through the new wrapper.
- docs/architecture/decisions/dd-036-safe-write-pattern.md +
  docs/architecture/security.md: document the callback-driven kernel and
  its scope.

Plan: 20260419-plan~Embeddings Atomic Write Hardening.md
```

## Rules

**Why, not what.** The diff shows what. The message explains why. If your message could be reconstructed by reading the diff, it is not earning its keep.

**Specific identifiers over category nouns.** Name the function, file, or flag by its actual identifier. A reader searching `git log` for `safe_write_via` should find this commit; a reader searching for `atomic write kernel` should also find it, but the specific identifier is the one that cannot be confused with anything else.

**One logical change per commit.** If a single commit message needs two paragraphs to describe two unrelated changes, split the commit. The exception is coordinated changes that only make sense together, for example renaming a function and updating every call site.

**Never combine unrelated changes** even when staging is convenient. Logical coherence over commit count.

**Match recent `git log` style.** Subject conventions drift over time; always check the last 10–20 commits before writing.

## When The Rules Do Not Apply

Trivial commits do not need a body:

```text
Fix typo in docs/user/user-reference.md
Bump dev dependency: pytest 8.3 -> 8.4
```

If there is genuinely nothing to explain beyond the subject, do not invent filler. But most commits in this repo are not trivial; when in doubt, write the body.
