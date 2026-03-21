# Friction Logs

Signal accumulator for maintenance — agents log when they can't find something, hit conflicting info, or have to guess.

## Style

Suggested colour: sky → rose blend (`#AFB2DB`). See `[[.brain-core/colours]]` for the temporal blend formula. Use `style.css` as-is or adapt to your vault's colour scheme.

## Install

```
_Config/Taxonomy/Temporal/friction-logs.md   ← taxonomy.md
_Config/Templates/Temporal/Friction Logs.md  ← template.md
_Temporal/Friction Logs/                     ← create folder
style.css                                    ← merge into .obsidian/snippets/folder-colours.css
```

### Router trigger (optional)

```
- When encountering missing context or conflicting info → [[_Config/Taxonomy/Temporal/friction-logs]]
```
