# Adding a Temporal Child Folder

1. Create the folder under `_Temporal/`.
2. Add a conditional trigger to the router if the type has one.
3. Create a taxonomy file at `_Config/Taxonomy/Temporal/{name}.md` describing the type's purpose, conventions, and template.
4. If artefacts of this type can originate from or spin out to other artefacts, reference the [provenance standard](../provenance.md) in the taxonomy.
5. Run `brain_action("compile")` to regenerate the router and colours — CSS is auto-generated, rose blend applied automatically.
6. Log the addition.
