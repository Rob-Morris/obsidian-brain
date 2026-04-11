# Agent Workflow Tiers

Match the amount of process to the size and risk of the task.

Default to the smallest tier that safely fits. Escalate one tier when the blast radius is unclear, the change crosses bounded contexts, or it touches bootstrap, migrations, routing, or security-sensitive behaviour.

## Tiers

### `trivial`

Use for narrow, local changes: typo fixes, doc clarifications, template wording, or config-only tweaks with no behavioural effect.

Flow: implement -> verify

### `small`

Use for clear single-file or single-context changes where the contract is already understood.

Flow: implement -> verify -> review

### `medium`

Use for multi-file work, new flows inside one or two contexts, or changes with meaningful edge-case risk.

Flow: research -> plan -> implement -> verify -> review

### `large`

Use for architectural work, migrations, bootstrap/security changes, new context boundaries, or anything that needs staged rollout.

Flow: research -> design -> approval -> plan -> implement -> verify -> multi-review -> final review

## Escalation Signals

- You are touching more files or contexts than expected
- The change alters a shared contract, not just one caller
- The verification path is broad or expensive
- Rollback would be hard or risky
- The user asked for design review before implementation

When a task trips one of these signals, step up a tier before proceeding.
