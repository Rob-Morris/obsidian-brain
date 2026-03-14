# Triggers

Triggers are workflow rules that govern agent behaviour throughout a session. They fire at specific moments — before actions, after actions, or continuously during work.

## Three Categories

### Before

Fire before the agent takes a specific action. Used for safety checks, planning steps, and confirmation prompts.

### After

Fire after a specific event occurs. Used for logging, capturing artefacts, and updating state.

### Ongoing

Active throughout the session. Used for behavioural constraints and periodic re-checks.

## Where Triggers Live

Active triggers are listed in the vault's **router** file. The router is the single source of truth for which triggers are currently active. Each vault configures its own set.

## Writing Good Triggers

- Start with the timing word: "Before...", "After...", "During..."
- Be specific about what event fires the trigger
- Be specific about what action to take
- Keep each trigger to one sentence where possible
- Include the target path or location for any file operations

## Managing Triggers

**Adding:** Add a line under the appropriate category in the router.

**Changing:** Edit the line in the router. If the change affects a core workflow (e.g. where logs are stored), update the relevant artefact type in the router's table too.

**Removing:** Delete the line from the router. If the trigger referenced a folder that's no longer needed, consider removing the artefact type as well.

## Example Triggers

These are examples, not defaults. Pick what suits your vault.

**Before:**
- Before taking action, ask clarifying questions.
- Before taking action, show a brief plan.
- Before creating any file, confirm it has a home in the vault. If no folder fits, extend the vault first.
- Before deleting files, ask for explicit user approval.

**After:**
- After completing meaningful work, append a timestamped entry to the day's log.
- After refining an artefact through Q&A, capture the raw Q&A in a transcript.

**Ongoing:**
- During long sessions, re-read triggers before and after each block of work.
