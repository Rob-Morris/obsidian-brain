# Canary Pattern

A canary is a lightweight verification mechanism for steps that are subjective, hard to test automatically, or easily skipped. It works by requiring the actor (human or agent) to write a structured log proving they followed the instructions.

## How It Works

1. A **canary file** describes a trigger and numbered action items. It includes or links to all the context needed to follow them.
2. After completing the actions, write a **canary log** at a known location listing each item as `done` or `skip: reason`.
3. A **hook or check** verifies the log exists and covers all items.

The insight: an actor that read the instructions and wrote the log in the correct format almost certainly followed the instructions too. If it didn't, the log will be missing or incomplete.

## Log Format

Filename convention: `.canary--{name}` (e.g. `.canary--pre-commit`)

```
[1] done
[2] done
[3] skip: no user impact
```

Each numbered line corresponds to a numbered item in the canary file. Every item must be either `done` or `skip: {reason}`.

## Properties

- **Transient** — canary logs are gate files, not permanent records. Hooks should delete them after a successful check so they can't go stale.
- **Self-enforcing** — adding a numbered item to the canary file automatically raises the bar. No hook changes needed.
- **Gitignored** — canary logs are working files, not committed. Add `.canary--*` to `.gitignore`.

## Adapting for Your Project

1. **Define your canary** — create a file describing the trigger (when does this fire?) and numbered action items.
2. **Choose a log location** — repo root is typical. Use the `.canary--{name}` naming convention.
3. **Add a hook** — a git hook, CI step, or script that checks the log exists and covers all items.
4. **Gitignore the logs** — add `.canary--*` to your `.gitignore`.
5. **Document it** — tell contributors where to find the canary file and how to write the log.

The pattern scales by adding canary files, not by modifying hooks. Each canary is self-contained.
