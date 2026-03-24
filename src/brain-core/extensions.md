# Extensions

When the vault needs a new artefact type, follow the procedure for the relevant tier. Log what was added and why in the day's log.

## When to Add a New Type

Before creating a new artefact type, check these criteria:

- **No existing type fits** — the content doesn't belong in any current folder, even with generous interpretation
- **Recurring pattern** — you expect multiple files of this kind, not just one
- **Distinct lifecycle** — the content has different naming, frontmatter, or archiving rules from existing types
- **Worth the overhead** — each type needs a taxonomy file and a router trigger (colours are auto-generated). If the content is a one-off, a subfolder or tag within an existing type may be simpler

## Adding a Living Artefact Folder

1. Create the folder at vault root.
2. Add a conditional trigger to the router if the type has one.
3. Create a taxonomy file at `_Config/Taxonomy/Living/{name}.md` describing the type's purpose, conventions, and template.
4. If artefacts of this type can originate from or spin out to other artefacts, reference [[.brain-core/standards/provenance]] in the taxonomy.
5. Run `brain_action("compile")` to regenerate the router and colours — CSS is auto-generated.
6. Log the addition.

## Adding a Temporal Child Folder

1. Create the folder under `_Temporal/`.
2. Add a conditional trigger to the router if the type has one.
3. Create a taxonomy file at `_Config/Taxonomy/Temporal/{name}.md` describing the type's purpose, conventions, and template.
4. If artefacts of this type can originate from or spin out to other artefacts, reference [[.brain-core/standards/provenance]] in the taxonomy.
5. Run `brain_action("compile")` to regenerate the router and colours — CSS is auto-generated, rose blend applied automatically.
6. Log the addition.

## Adding a Config Child Folder

1. Create the folder under `_Config/`.
2. No CSS changes needed — inherits config purple styling.
3. Document in the router if relevant.

## Adding a Plugin Folder

1. Create the folder under `_Plugins/`.
2. No CSS changes needed — inherits gold plugin styling.
3. Document in the router.
4. Create a skill in `_Config/Skills/` if the plugin has MCP tools or CLI commands.

## Adding a Memory

Memories are reference cards — factual context about projects, tools, or concepts that agents load on demand when the user references something they're expected to know about.

**Memories vs skills:** Memories answer "what is it?" — what something is, where it lives, how pieces relate. Skills answer "how do I do it?" — step-by-step procedures and tool usage. If a memory starts containing steps to follow, it should be a skill instead. A memory can reference a skill ("For deployment, see the deploy skill") but should not replicate it.

1. Create a `.md` file in `_Config/Memories/` with `triggers: [...]` in YAML frontmatter. Triggers are the words or phrases the user might use when referencing this concept.
2. Write a factual reference card body — what something is, where to find things, key facts. If you're writing a procedure, create a skill in `_Config/Skills/` instead.
3. Run `brain_action("compile")` to include the memory in the compiled router.
4. Update the `_Config/Memories/README.md` table so agents without MCP/compiler can find it via the naive fallback path.

## Extending Principles

System-level always-rules live in `index.md`'s Principles section. Vault-specific additions go in the router's `Always:` section. Add each as a bullet with a short description explaining the constraint. The compiler merges both — system rules first, vault additions after.

---

See [[.brain-core/artefact-library/README]] for ready-to-use artefact type definitions.
