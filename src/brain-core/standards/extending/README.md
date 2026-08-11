# Extending the Vault

How to extend the vault. Follow the relevant link below.

## Adding Types

- [Decide when a new type is warranted](when-to-add-type.md)
- [Add a living artefact type](adding-living-type.md)
- [Add a temporal artefact type](adding-temporal-type.md)

For a custom type, prefer the guarded `brain type create` CLI after drafting
the taxonomy and linked template. It validates and writes the taxonomy,
template, and discoverable artefact folder as one bundle. Use `brain trigger
create` for the optional router entry. `brain command describe type.create
--json` and `brain command describe trigger.create --json` expose the exact
request schemas. Direct file steps in the detailed guides remain useful for
understanding and recovery.

## Other Extensions

- [Add a memory](adding-memory.md)
- [Extend the core principles](extending-principles.md)

---

For ready-to-use artefact type definitions, see [the artefact library](../../artefact-library/README.md).
