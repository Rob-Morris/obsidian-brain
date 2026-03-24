# Adding a Living Artefact Folder

1. Create the folder at vault root.
2. Add a conditional trigger to the router if the type has one.
3. Create a taxonomy file at `_Config/Taxonomy/Living/{name}.md` describing the type's purpose, conventions, and template.
4. If artefacts of this type can originate from or spin out to other artefacts, reference [[.brain-core/standards/provenance]] in the taxonomy.
5. Run `brain_action("compile")` to regenerate the router and colours — CSS is auto-generated.
6. Log the addition.
