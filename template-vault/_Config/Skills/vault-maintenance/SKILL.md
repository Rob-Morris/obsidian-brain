---
name: vault-maintenance
description: |
  Enforces vault workflow rules when working on artefacts in the Brain Obsidian vault. Triggers whenever the user asks to create, edit, refine, or reorganise content in the vault — wiki pages, logs, transcripts, or vault structure itself. Also triggers for vault maintenance tasks like refactoring, restructuring, or bulk updates. Use this skill any time work will result in files being created or modified in the vault, even if the user doesn't explicitly mention "vault" or "maintenance".
---

# Vault Maintenance

Read the vault's router, then follow its workflow triggers throughout the session. The router is the single source of truth — this skill just makes sure you read and follow it.

## Session Start

1. Read `_Config/router.md` — contains the **Workflow Triggers** (Before/After/Ongoing) and artefact type map. These govern your behaviour for the whole session.
2. Read `_Config/Styles/writing.md` — language and tone rules.
3. Read the relevant taxonomy file(s) in `_Config/Taxonomy/` for any artefact types you'll be working with.

## After Each Block of Work

Run the session hygiene check:

```bash
python _Config/Skills/vault-maintenance/scripts/compliance_check.py
```

Fix any issues before moving on.

## Structural Compliance

For deep structural compliance (naming, frontmatter, archive metadata, status values), run:

```bash
python3 .brain-core/scripts/check.py
```

Use `--json` for structured output, `--actionable` for fix suggestions, `--severity warning` to filter.

## Session End

Run the compliance check a final time.

## Priority

If anything in this skill conflicts with the router or core docs, the router wins.
