# Adding a Memory

Memories are reference cards — factual context about projects, tools, or concepts that agents load on demand when the user references something they're expected to know about.

**Memories vs skills:** Memories answer "what is it?" — what something is, where it lives, how pieces relate. Skills answer "how do I do it?" — step-by-step procedures and tool usage. If a memory starts containing steps to follow, it should be a skill instead. A memory can reference a skill ("For deployment, see the deploy skill") but should not replicate it.

1. Create a `.md` file in `_Config/Memories/` with `triggers: [...]` in YAML frontmatter. Triggers are the words or phrases the user might use when referencing this concept.
2. Write a factual reference card body — what something is, where to find things, key facts. If you're writing a procedure, create a skill in `_Config/Skills/` instead.
3. Run `python3 .brain-core/scripts/compile_router.py` to include the memory in the compiled router.
4. Update the `_Config/Memories/README.md` table so agents without MCP/compiler can find it via the naive fallback path.
