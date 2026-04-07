# Documentation

Router for brain-core's development documentation.

## User Layer — How to Use It

- [Getting Started](user/getting-started.md) — installation, first vault, orientation
- [System Guide](user/system-guide.md) — artefact system mechanics: lifecycle, frontmatter, statuses, compliance, extension
- [Template Library Guide](user/template-library-guide.md) — default template library: what ships, what each type is for
- [Workflows](user/workflows.md) — day-to-day usage patterns, MCP tool workflows

## Functional Layer — What It Does

- [MCP Tools](functional/mcp-tools.md) — MCP tool specifications
- [Scripts](functional/scripts.md) — script reference: entry points, arguments, behaviour
- [Config](functional/config.md) — configuration: profiles, merge rules, environment

## Architectural Layer — How/Why It's Built This Way

- [Architecture Overview](architecture/overview.md) — system architecture: components, data flow, boundaries
- [Design Decisions](architecture/decisions/) — decision index + per-decision records
- [Security](architecture/security.md) — path boundary model, privilege split, write guards

## Other

- [Specification](specification.md) — vault specification
- [Contributing](contributing.md) — how to contribute (humans)
- [Contributing — Agents](contributing-agents.md) — how to contribute (agents)
- [Plugins](plugins.md) — plugin system
- [Canary](canary.md) — pre-commit canary documentation
- [Changelog](changelog.md) — release history

> **Migration note:** This structure is being built incrementally. Some links point to files that don't exist yet — they'll be created as the migration progresses. During migration, the original files (`tooling.md`, `user-reference.md`, `user-guide.md`) remain authoritative until their replacements are complete.
