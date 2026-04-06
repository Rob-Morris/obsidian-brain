# Memories

Memories are reference cards that agents load on demand — factual context about projects, tools, and concepts that the user expects agents to know about.

**Memories answer "what is it?"** — what something is, where it lives, how pieces relate, key facts. They're context that informs decisions.

**Skills answer "how do I do it?"** — step-by-step procedures, tool usage, decision trees. If you find yourself writing steps to follow, create a skill in `_Config/Skills/` instead. A memory can point to a skill ("For deployment, see the deploy skill") but should not duplicate it.

## Using Memories

### With MCP

```
brain_list(resource="memory")               # list all memories
brain_read(resource="memory", name="brain")  # search by trigger
```

Trigger matching is case-insensitive substring: "brain" matches a memory with trigger "brain core".

### Without MCP

Scan the table below. When a trigger matches what the user is referencing, read the linked file.

## Available Memories

| Trigger | File |
|---|---|
| brain core, obsidian-brain, brain system, vault system | [[_Config/Memories/brain-core-reference]] |

## Creating a Memory

1. Create a `.md` file in `_Config/Memories/` with `triggers: [...]` in YAML frontmatter
2. Write a factual reference card body — what something is, where to find things, key facts. If you're writing steps to follow, that's a skill, not a memory
3. Run `brain_action("compile")` to include the memory in the compiled router
4. Update this table so naive agents (no compiler/MCP) can find it too
