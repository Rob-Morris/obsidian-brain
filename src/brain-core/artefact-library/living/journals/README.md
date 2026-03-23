# Journals

Named journal streams. One file per journal, grouping journal entries via nested tags.

## Install

```
_Config/Taxonomy/Living/journals.md      ← taxonomy.md
_Config/Templates/Living/Journals.md     ← template.md
Journals/                                ← create folder
```

### Router trigger (optional)

```
- When the user wants to journal or reflect on their life → [[_Config/Taxonomy/Temporal/journal-entries]]
```

Note: the router trigger points to the journal entries taxonomy (the temporal type), since that's where the creation workflows live. The journal (living) file is created once as a container and rarely needs a trigger.
