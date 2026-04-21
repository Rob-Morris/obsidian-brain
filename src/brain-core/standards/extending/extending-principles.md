# Extending Principles

System-level always-rules live in `session-core.md`'s `Always:` section. `index.md` is the bootstrap entry point that routes agents to `session-core.md` and the rest of the core docs; it is not the source of truth for those rules. Vault-specific additions go in `_Config/router.md`'s `Always:` section. Add each as a bullet with a short description explaining the constraint. The compiler merges both — system rules first, vault additions after.
