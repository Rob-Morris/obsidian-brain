# Printables

Paginated PDF documents generated from markdown using Pandoc. Source is markdown; output is PDF in `_Assets/Generated/Printables/`.

## Install

```
_Config/Taxonomy/Temporal/printables.md         <- taxonomy.md
_Config/Templates/Temporal/Printables.md        <- template.md
_Config/Skills/printables/SKILL.md              <- SKILL.md
_Config/Skills/printables/base.tex              <- base.tex
_Config/Skills/printables/keep-headings.tex     <- keep-headings.tex
_Temporal/Printables/                           <- create folder
```

### Router trigger (optional)

```
- When converting vault content into a printable PDF or handout → [[_Config/Taxonomy/Temporal/printables]]
```
