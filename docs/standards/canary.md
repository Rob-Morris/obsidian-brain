# canary.md

This document describes the Canary Brief, a technique for testing subjective work by agents. Each brief specifies a list of tasks with testable instructions for logging what was done. If the log is missing or malformed, the test fails — check the receipt, not the work.

## How to use a canary brief

1. Create a canary brief `my-canary-test.md`
2. Write a canary brief using the approach below. Use the template or make your own version.
3. Test if the log file is present and correctly formatted. If so, the test passes.
4. Recommendations:
    a. Use an automated script or hook, like a pre-commit hook, to check the log
    b. Log testing should parse tasks from the brief at runtime, so that the brief is self-enforcing and the source of truth. When you extend the brief tasks, they are automatically tested.
    c. Prefix logs with `.canary--` and gitignore this prefix
    d. Suffix logs with the brief name, e.g. `.canary--my-canary-test`
    e. Store briefs in `.canaries/` at the repo root — they're operational, not documentation

## When to use and not to use

Use a canary brief to provide an agent with a testable list of tasks, especially when task completion is subjective. If work can be tested deterministically, that is more reliable. For everything else, there's canary.md. A canary brief can be used as a task list and supplemented by deterministic tests.

## Why it scales

The pattern is self-enforcing. When you add a new bracket ID to the canary brief, the hook automatically requires it. No hook changes, no config updates. The canary definition is the single source of truth. You extend the checklist, the bar rises.

Canary logs are transient by design. They're gate files, not permanent records. Gitignore them. They exist to prove the work was done, then they disappear. You could also modify the canary approach to save logs somewhere as an audit trail, with or without automated testing.

Use this pattern to verify whether any kind of subjective steps were followed: doc updates, cross-referencing, style checks, migration steps, versioning/release hygiene, shared-fact cross-checks, whatever you need. The pattern scales by adding canary files, not by modifying hooks. Each canary is self-contained. A standard hook should be able to test any canary file.

# The Canary Brief

A canary brief succinctly describes a list of tasks with optional triggers for performing or skipping the task, any specifics needed to perform the task, and an instruction to write a log of what was done in a specific format. For more complex briefs, you can use nested sub-items.

A canary brief has three parts:
1. **Context**: A name, trigger condition, and optionally any general context
2. **Tasks**: A list of tasks with a unique ID, optional trigger, instructions and any needed context
3. **Log**: Instructions for writing the log

If a canary brief is intended for the local environment, use a `local.md` suffix and gitignore it.

## Context

- A heading starting with "Canary: " followed by the name
- Any trigger conditions or instructions (optional)
- Any additional context (optional, keep minimal)

### Context example
```markdown
# Canary: {name}

Run before every commit.
```

## Tasks

One task per line. Nesting allowed, optionally indent for readability.

Task format: `[id] **short name** condition: instruction`

The `[id]` brackets are literal (they appear in the output). Condition is optional — omit it and the colon when the task always applies.
- `[id]` — unique alphanumeric identifier; nested item IDs must start with parent ID (e.g. `[1]` → `[1a]`)
- `**short name**` — a short, scannable label
- condition (optional) — "if" or "skip if" guidance on when the instruction should be performed or skipped, always followed by `:` if present
- instruction — what the agent should do

A task can point at a deterministic verification command plus a conditional
repair command. Example: "Run `make sync-template-check`; if it reports drift,
run `make sync-template` and log both outcomes." This keeps the canary focused
on proving the workflow was followed, not on re-implementing the underlying
checker in prose.

### Tasks example

```markdown
## Tasks

[1] **Update README** If new feature merged: ensure `README.md` reflects current usage and examples
[2] **Update Changelog** One entry per version number (semver), prepend new entries, summarise what changed and who it affects: `CHANGELOG.md`
[3] **Deprecation notices** If API surface removed: add inline deprecation warnings with target removal version
[4] **Tone review** Skip if minor typo fix: read docs from a new user's perspective, flag jargon or assumed knowledge
  [4a] **Glossary check** If new domain terms introduced: add to glossary at `docs/glossary.md` or define on first use
  [4b] **Code samples** Verify all code samples in `docs/examples/` still run against latest API
  [4c] **Style guide** Update docs to follow the style guide at `docs/STYLE_GUIDE.md`
[5] **Broken links** Scan all links under `docs/` and flag 404s and redirects
[6] **Screenshot currency** If UI changed: retake and replace outdated screenshots in `docs/assets/screenshots/`
[7] **Notify team** Skip if no docs changes: send docs update summary to `#docs-updates` on Slack
```

## Log

The log section instructs the agent to write a log file after completing the tasks. Logs should be named `.canary--{name}` and saved to a specific location.

### Log example

```markdown
## Log

After following the tasks above, ALWAYS write a log file named `.canary--{name}` to the repo root.
Every task must have exactly one log line with a matching ID. Optionally indent sub-items for readability.

Log format: `[id] Short name: status, comment`

The `[id]` brackets are literal. Comment is optional for `done`, required for `skip`.

- `[id]` must match a task ID from the Tasks section
- `Short name` must match the corresponding task name (plain text, no bold)
- Status must be `done` or `skip`
- Comment is optional for `done`, required for `skip`, always preceded by `, `

### done

`done` means you performed the action. Only write `done` if you actually did the thing. You must add detail after `skip`. You may optionally add detail after `done`, e.g. `done, updated 4 files` or `done, 353 passed`.

### skip

`skip, {reason}` means you did not perform the action. The reason must be your own assessment of why the action wasn't needed — describe what you evaluated and what you concluded. Do not copy example reasons from the brief; write what actually applies to your situation. Good skip reasons describe:
- what changed didn't reach this area (`skip, changes limited to test infrastructure`)
- the precondition doesn't apply (`skip, no new artefact types introduced`)
- you checked and found nothing to do (`skip, grepped for old value, no stale references`)

Note: `skip` uses a comma separator (not colon) to avoid ambiguity with the label separator.
```

# The Canary Log

## Log output example

```
[1] Update README: done
[2] Changelog entry: done, added v2.4.0 section
[3] Deprecation notices: skip, no API surface removed
[4] Tone review: done
  [4a] Glossary check: skip, no new domain terms introduced
  [4b] Code samples: done, fixed auth example
  [4c] Style guide: done
[5] Broken links: done, replaced 2 dead links
[6] Screenshot currency: skip, no UI changes
[7] Notify team: done
```

# Testing the log

Write a script or hook (git hook, CI step, whatever) to test the log.

Local vs committed:
- Use `.git/hooks/pre-commit` for a local repository test
- Use `.githooks/pre-commit` and set it active with `git config core.hooksPath .githooks` for a hook distributed with the repo

At runtime:
1. Verify that the log file exists. If not, the test fails.
2. Extract all task lines from the brief's `## Tasks` section. Task lines must start with a bracket ID (e.g. `[1]`, `[4a]`), ignoring whitespace.
3. Parse the log file, ensuring each task ID is matched by a correctly formatted log line. If anything is malformed, the test fails.
4. Delete the log file to prevent staleness.

Note: You may optionally bundle additional tests. For example, if a task specifies touching a particular file, check that the file has changes.

## Example git pre-commit hook

```bash
#!/usr/bin/env bash
#
# Pre-commit hook: verifies .canary--pre-commit covers all bracket IDs
# defined in .canaries/pre-commit.md.
#
# The canary brief is the source of truth — adding a new [id] there
# automatically enforces it here.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CANARY_DEF="$REPO_ROOT/.canaries/pre-commit.md"
CANARY_LOG="$REPO_ROOT/.canary--pre-commit"

if [[ ! -f "$CANARY_DEF" ]]; then
  echo "pre-commit: .canaries/pre-commit.md not found — skipping canary check"
  exit 0
fi

# Extract all [id] patterns from the Tasks section (between ## Tasks and ## Log)
tasks_section=$(sed -n '/^## Tasks$/,/^## Log$/p' "$CANARY_DEF")
expected_ids=($(echo "$tasks_section" | grep -oE '\[[0-9]+[a-z]?\]' | sort -u))

if [[ ${#expected_ids[@]} -eq 0 ]]; then
  echo "pre-commit: no bracket IDs found in .canaries/pre-commit.md — skipping"
  exit 0
fi

# Check canary log exists
if [[ ! -f "$CANARY_LOG" ]]; then
  echo ""
  echo "pre-commit: .canary--pre-commit not found."
  echo ""
  echo "Read .canaries/pre-commit.md and write .canary--pre-commit"
  echo "confirming you've followed each task. Format:"
  echo ""
  echo "  [1] Label: done"
  echo "  [2] Label: skip, reason"
  echo ""
  exit 1
fi

# Check each expected ID is present in the log
missing=()
for id in "${expected_ids[@]}"; do
  if ! grep -qF "$id" "$CANARY_LOG" 2>/dev/null; then
    missing+=("$id")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  echo ""
  echo "pre-commit: .canary--pre-commit is missing these tasks:"
  for task in "${missing[@]}"; do
    echo "  $task"
  done
  echo ""
  echo "Mark each as '[id] Label: done' or '[id] Label: skip, reason'."
  exit 1
fi

# Validate format: each bracketed line must have a label and status
bad=()
while IFS= read -r line; do
  trimmed="${line#"${line%%[![:space:]]*}"}"
  if [[ "$trimmed" =~ ^\[[0-9]+[a-z]?\] ]]; then
    if ! echo "$trimmed" | grep -qE '^\[[0-9]+[a-z]?\] .+: (done([, ] ?.+)?|skip, ?.+)$'; then
      bad+=("$line")
    fi
  fi
done < "$CANARY_LOG"

if [[ ${#bad[@]} -gt 0 ]]; then
  echo ""
  echo "pre-commit: these lines have invalid format:"
  for line in "${bad[@]}"; do
    echo "  $line"
  done
  echo ""
  echo "Expected: [id] Label: done  or  [id] Label: skip, reason"
  exit 1
fi

# Clean up — remove canary log so it can't go stale
rm -f "$CANARY_LOG"
```
