# Post-Propagation Canary

Follow after propagating brain-core to a vault (copying `src/brain-core/` to `.brain-core/`). See [standards/canary.md](../standards/canary.md) for how canaries work.

## Items

1. **Activity log updated.** Add a timestamped entry to `_Temporal/Logs/{yyyy-mm}/log--{date}.md` summarising what changed in each version propagated. Include version numbers.

2. **Daily note updated.** Add task checkboxes and a notes section to `Daily Notes/{date}.md` covering the work done. Update the cookie count if cookies were earned.

3. **Master design doc updated.** If the changes are architecturally significant (new systems, new patterns, changed tooling, new artefact types), update `Designs/brain-master-design.md`:
    - "What's Built" version reference and relevant sections
    - New subsections for new systems
    - "Outstanding Work" if items were completed or added
    - "Implementation Sequence" checkmarks if items were completed

    `skip` if the changes are minor (doc fixes, small patches, no architectural impact).

## Log

After following the items above, write `.canary--post-propagation` at the vault repo root:

```
[1] done
[2] done
[3] skip: doc-only patch, no architectural changes
```

This canary has no automated hook — it relies on the agent following the pre-commit canary's reference to this file.
