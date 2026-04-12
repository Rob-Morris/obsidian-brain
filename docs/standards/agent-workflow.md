# Agent Workflow

Contributor-only workflow standard for agents changing `obsidian-brain`.

This does not ship in `.brain-core` and is not part of the runtime bootstrap for normal Brain agents.

## Tiered Workflow

When making or planning changes, choose the smallest workflow tier that safely fits the change. Escalate one tier when the blast radius is unclear, the change crosses bounded contexts, or it touches bootstrap, migrations, routing, or security-sensitive behaviour.

| Tier | Use when | Required flow | Verification bar |
|---|---|---|---|
| `trivial` | Typos, doc clarifications, local config/template tweaks, dead-code removal with no behavioural effect | Implement -> verify | Concrete local proof: the changed text/config renders correctly or the targeted command/output matches the intent |
| `small` | Single-file or single-context behavioural changes with a clear contract | Implement -> verify -> review | Focused tests or equivalent reproducible check, then self-review the diff for drift |
| `medium` | Multi-file changes within one or two contexts, or work with meaningful edge-case risk | Research -> plan -> implement -> verify -> review | Explicit execution + verification plan, relevant tests, and a docs/canary sweep before hand-off |
| `large` | Architectural changes, new context boundaries, migrations, bootstrap/security changes, or staged rollouts | Research -> design -> approval -> plan -> implement -> verify -> multi-review -> final review | Full-suite proof, explicit rollout/rollback thinking, and separate review passes for design and implementation |

## Tier Notes

- `trivial` should stay local. If the change starts affecting behaviour, tests, or multiple files, it is no longer trivial.
- `small` is the default tier for straightforward brain-core implementation work. If a core domain behaviour changes, update an existing `.feature` file or add one when that contract is not already covered.
- `medium` requires an explicit plan before editing. Name the bounded contexts touched, the files you expect to change, and how you will verify the result.
- `large` work should be phased. Do not treat a cross-cutting design change as an auto-implement task; shape it, get approval where required, and then execute in verifiable slices.
