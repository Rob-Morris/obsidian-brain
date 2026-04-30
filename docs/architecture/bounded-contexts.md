# Bounded Context Map

brain-core is organised around a small set of bounded contexts. These are documentation boundaries first: they clarify ownership, import direction, and where new work belongs. They do not imply that every context must immediately become its own package or runtime service.

## Contexts

| Context | Responsibilities | Scripts |
|---|---|---|
| Compilation | Compile runtime artefacts from source definitions; keep generated router, colours, and search index current | `compile_router.py`, `compile_colours.py`, `build_index.py`, `sync_definitions.py` |
| Artefact Operations | Read and mutate vault content and config resources through the router contract | `create.py`, `edit.py`, `read.py`, `rename.py`, `fix_links.py`, `start_shaping.py`, `shape_printable.py`, `shape_presentation.py` |
| Compliance | Validate structure, naming, and taxonomy conformance | `check.py` |
| Content Intelligence | Search and enumerate content for retrieval workflows | `search_index.py`, `list_artefacts.py` |
| Session & Configuration | Assemble runtime config, bootstrap sessions, manage operator/auth state, resolve registered workspaces | `session.py`, `config.py`, `workspace_registry.py`, `generate_key.py` |
| Lifecycle Management | Install, upgrade, and migrate the engine and vault naming conventions over time | `init.py`, `upgrade.py`, `migrate_naming.py`, `migrations/` |
| MCP Integration | Expose script capabilities over MCP transport, enforce tool-level resilience and profile gates | `brain_mcp/server.py`, `brain_mcp/proxy.py`, `brain_mcp/_server_*.py` |
| Platform Integration | Bridge to external platform capabilities that are not part of the core domain model | `obsidian_cli.py` |

## Import Policy

### Stable dependency direction

Dependencies should point inward toward lower-level shared capabilities, not sideways across peer contexts.

- All contexts may depend on `_common/` public API.
- MCP Integration may depend on any script context because it is an adapter layer over the core operations.
- Platform Integration should remain a leaf adapter: other contexts may call it, but it should not import domain scripts.
- Compilation outputs are consumed by Artefact Operations, Compliance, Content Intelligence, and Session & Configuration, but compilation scripts should not import those higher-level contexts to do their work.
- Lifecycle Management may orchestrate other contexts at process boundaries, but should avoid reaching into private helpers across contexts.

### Public versus private code

- `_common/__init__.py` is the shared-kernel facade. Cross-context helpers should be promoted there as explicit public API.
- Underscore-prefixed helpers inside individual modules stay private to that module unless promoted.
- Prefer importing a script's documented top-level function over reaching into its internal helpers from another context.
- If a new capability is broadly shared and not naturally owned by an existing context, add it to `_common/` instead of creating ad hoc cross-context imports.

### Practical guidance for new work

- New user-visible vault operations usually belong in Artefact Operations.
- New validation rules belong in Compliance, even if they inspect content produced elsewhere.
- Search and listing features belong in Content Intelligence.
- Config loading, session bootstrap, and operator policy belong in Session & Configuration.
- Install, upgrade, and migration flows belong in Lifecycle Management.
- MCP server concerns stay in MCP Integration; do not move domain logic there.

## Why This Matters

This map gives the repo a shared language for refactors. When a file feels overloaded, the first question is which bounded context owns the behaviour. When an import feels awkward, the first check is whether it crosses a context boundary through a private seam.
