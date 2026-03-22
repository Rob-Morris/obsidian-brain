# canary.md

If you're doing complex work with agents, it's important to test their work. But how do you test an agent's work when the output is subjective?

canary.md might be what you're looking for. It's two things: a brief and a log. The brief is a set of instructions asking an agent to take specific actions then write a log to a temporary "canary" file with a specific name and format.

If the agent didn't follow your instructions, the canary will be dead: the log file will either be missing or written in the wrong format.

All you need to implement this:

1. A `canary.md` in your project docs that describes the specifics of building a canary brief + your specific canary log format.
2. For each set of things you want to test, make a canary brief.
3. Add any kind of testing hook to test that the canary log is there and in the right format. If it is, it's a pass and the log is deleted, otherwise it's a fail.

That's it.

## The canary brief

A canary brief succinctly describes a numbered list of triggers & actions for an agent to perform, plus any additional context needed to perform the actions, with the final instruction being to write a log of what was done in a specific format. For more complex briefs, you can use a routing table.

### Brief format

```markdown
# {Name} Canary

{One-line trigger — when this canary fires.} See [standards/canary.md](...) for how canaries work.

## Items

1. **{Short name}.** {Instruction with enough detail to act on.}

2. **{Short name}.** {Instruction.}

3. **{Short name}.** {Instruction. For complex items, add sub-items:}
    - Sub-item A
    - Sub-item B

    `skip` if {condition when skipping is acceptable}.

## Log

After following the items above, write `.canary--{name}` at {location}:

    [1] done
    [2] done
    [3] skip: {realistic example reason}
```

Each item gets a **bold short name** so it's scannable, followed by the full instruction. Blank lines between items for readability. Line 2 links back to this standard so agents can look up the format.

Only items that are conditionally applicable should include `skip` guidance. Always-do items (like "run the tests") don't need it — they must always be done.

### Example

A "pre-commit" canary might list: run the tests, bump the version, update the changelog, update every doc affected by your change (with a routing table mapping change types to the files that need updating), and cross-check shared facts to catch stale references. A simple version of that list might look like:

1. **Tests pass.** Run `make test`. All tests must pass.

2. **Version bumped.** Increment `./VERSION` if there are any functional changes. Use semver.

3. **Changelog updated.** Add a new entry at the top of `./changelog.md`. Format: `## v{x.y.z} — YYYY-MM-DD`.

4. **Shared facts cross-checked.** Grep for the specific values you changed to catch stale references in any documentation.

5. **User docs updated.** Update user-facing docs if the changes impact users. `skip` if changes are internal only.

## The canary log

The final section of the brief instructs the agent to write a terse canary log at a known location. Filename convention: `.canary--{name}` (e.g. `.canary--pre-commit`). The log is a numbered list where each line is either `done` or `skip: reason`:

```
[1] done
[2] done
[3] done
[4] done
[5] skip: no user impact
```

Log line numbers must match item numbers in the brief. `[1]` is item 1, `[2]` is item 2. No gaps, no reordering. Every item must be accounted for.

- **`done`** means you performed the action. Only write `done` if you actually did the thing.
- **`skip: {reason}`** means you did not perform the action, and the reason explains why. Reasons should be specific enough to audit — `skip: not applicable` is too vague; `skip: no functional changes` or `skip: only touched test files` tells a reviewer what was evaluated and why the action wasn't needed.

## The hook

A hook (git hook, CI step, whatever) checks the log. It counts the numbered items in the canary definition, then verifies the log exists, covers every item, and each line says `done` or `skip: reason`. If anything's missing or malformed, the commit is blocked.

You can often bundle additional tests. If a step says update a specific document, you can test if that document received an update.

After a successful check, the hook deletes the log so it can't go stale. Next time the trigger fires, the agent writes a fresh one.

## You don't need to check the work, just check the receipt

An agent that read the instructions, followed them, and wrote the log in the correct format almost certainly did the work. If it didn't, the log will be missing, incomplete, or in the wrong format.

## Why it scales

The pattern is self-enforcing. When you add a new numbered item to the canary brief, the hook automatically requires it. No hook changes, no config updates. The canary definition is the single source of truth. You extend the checklist, the bar rises.

Canary logs are transient by design. They're gate files, not permanent records. Gitignore them. They exist to prove the work was done, then they disappear.

Use this pattern to verify whether any kind of subjective steps were followed: doc updates, cross-referencing, style checks, migration steps, whatever you need. The pattern scales by adding canary files, not by modifying hooks. Each canary is self-contained.

## When not to use it

If your agent's work can be tested with deterministic test tooling, then that's the most reliable option. For everything else, there's canary.md.
