# Wikilinks

When and how to use wikilinks so they stay consistent across the vault. See [linking.md](linking.md) for resolution mechanics and collision handling.

## Core Rule

**Only wikilink to targets that exist in the vault.**

- If the artefact exists: `[[Artefact Name]]` — use the canonical basename.
- If the artefact does not exist: write plain text. Add the wikilink when the artefact is created.
- If the reference has no artefact and never will: keep it as plain text — wikilinks are for brain-managed artefacts, not arbitrary names.

Dangling wikilinks are hostile to the vault: clicking one in Obsidian creates a file at the vault root, bypassing the artefact system. Create the artefact first, then link.

## Scope Boundary

Wikilink consistency only applies to **brain-managed space** — files inside the vault's file index. Two categories are out of scope:

- **External vault content** — files outside brain-managed folders. The brain doesn't own that space and won't validate links into it.
- **Workspace content** — embedded (`_Workspaces/`) or linked workspaces use a separate namespace (`workspace://`) and aren't reached through wikilinks.

If a link's target lives outside brain-managed space, use plain text or a markdown link.

## Check and Fix

`brain_create` and `brain_edit` run a per-file check after every write and warn when the artefact contains broken, resolvable, or ambiguous wikilinks. Warnings appear in the tool response but never block the write:

```
⚠ Broken wikilinks: [[Helix]], [[Skogarmaor]]
⚠ Resolvable wikilinks (use fix-links to fix all or selected):
  [[Graph memory session management]] → [[20260407-report~Graph Memory Session Management System]]
⚠ Ambiguous wikilinks: [[dup]] matches 2 files
```

A link is **resolvable** when the fixer can map the written stem to a canonical file via naming heuristics (slug → title, temporal prefix, etc.). It's **broken** when no target matches, and **ambiguous** when multiple files share the basename.

### `brain_action("fix-links", ...)`

Single-file and vault-wide modes share one action:

```python
# Vault-wide scan (dry run)
brain_action("fix-links")

# Vault-wide apply
brain_action("fix-links", {"fix": True})

# Single file scan
brain_action("fix-links", {"path": "People/Fidel.md"})

# Single file, fix every resolvable link
brain_action("fix-links", {"path": "People/Fidel.md", "fix": True})

# Single file, fix only the named links
brain_action("fix-links", {"path": "People/Fidel.md", "fix": True,
                           "links": ["Graph memory session management"]})
```

`links` is optional — omit to apply every resolvable fix in the file. The param is named `links` (not `stems`) to match the warning output agents see on the preceding create/edit call.

### `fix_links` convenience flag

`brain_create` and `brain_edit` accept an optional `fix_links` boolean (default `false`). When `true`, every resolvable link in the written artefact is rewritten to its canonical target immediately after the write. Remaining broken or ambiguous links are still reported as warnings.

```python
brain_edit("edit", path="People/Fidel.md", body=..., fix_links=True)
```

Use `fix_links=True` when you're confident the resolvable suggestions are correct — e.g. you wrote the slug form and the fixer is mapping it to the canonical title. Leave it off (the default) when you want to review suggestions before applying.

## File Index Trade-off

The per-file check builds a vault file index via `os.walk`. For single create/edit calls this costs milliseconds and is acceptable. Callers that already hold an index (the vault-wide compliance checker, batch fixers) pass it in via the `file_index` param to avoid redundant walks. Temporal prefixes share the same walk and are passed through the `temporal_prefixes` param.

There is no caching in the current implementation. If high-frequency batch operations ever need it, the optimisation seam is already in place — hand a pre-built index to `check_wikilinks_in_file` and `scan_and_resolve_file`.

## For Agents Without MCP Tools

If you're working with the vault directly:

1. Before writing `[[X]]`, confirm `X.md` exists in the vault.
2. Prefer basename form. Path-qualified links (`[[Folder/X]]`) break on moves.
3. If the target doesn't exist, write plain text and create the artefact — then link.
4. After a rename, update every `[[old-name` reference. `scripts/rename.py:rename_and_update_links` does this automatically when used.
