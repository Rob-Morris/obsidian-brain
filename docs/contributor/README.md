# Contributor Documentation

How to contribute to Obsidian Brain. This layer covers contributor-facing product docs, repo workflow guidance, and agent-specific contributor instructions.

Use `make test` for the canonical serial suite and `make lint` for both
reusable-script docstrings and supported command API/schema documentation. The
[agent instructions](agents.md) explain that documentation boundary.

- [Specification](specification.md) — design rationale and structural decisions
- [Agents](agents.md) — contributor workflow guidance for agents
- [Dependencies](dependencies.md) — locked exports, offline checks and native release certification
- [Promotion Recovery](promotion-recovery.md) — rebuild an unpublished queue after an exceptional direct-main push
- [Plugins](plugins.md) — writing and packaging plugin integrations
- [Disposable Brain Linux Environments](../../tools/brain-lab/README.md) — reproducible Docker-backed installation, upgrade, diagnosis, and user-vault reproduction
- [Contributing](../CONTRIBUTING.md) — general contributor guide and maintenance rules, including deterministic repository contracts, subjective canary review, testing, version surfaces, and commit hygiene
- [Agent Workflow](../standards/agent-workflow.md) — contributor workflow tiers and mandatory post-push CI follow-through
- [Canary](../standards/canary.md) — canary system standard
- [Changelog](../standards/changelog.md) — tiered public release-history standard
- [Commit Messages](../standards/commit-messages.md) — release Summary subjects plus required `docs:` / `test:` / `chore:` prefixes for non-versioned commits
