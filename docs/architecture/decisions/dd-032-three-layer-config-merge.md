# DD-032: Three-layer config merge

**Status:** Implemented

## Context

Brain vault configuration needs to serve three distinct audiences simultaneously:

1. **Shipped defaults** — Sensible out-of-the-box behaviour that works without any configuration file.
2. **Vault operators** — Per-vault settings that should be committed to the vault's git repository and shared across all machines (operator profiles, brain name, artefact types).
3. **Individual machines** — Machine-local preferences (API keys, local flags, personal overrides) that must not be committed to a shared repository.

A single-file approach cannot satisfy all three: defaults would be wiped on customisation, and local-only settings would pollute shared config. A two-file approach (shared + local) lacks shipped defaults, making the system brittle if config files are absent.

## Decision

Configuration is loaded by `load_config()` across three layers in precedence order:

- **Layer 0 (template):** `defaults/config.yaml` — shipped with brain-core. Never edited by users.
- **Layer 1 (vault):** `.brain/config.yaml` — committed to the vault. Shared across all machines.
- **Layer 2 (local):** `.brain/local/config.yaml` — gitignored. Machine-specific only.

The config is divided into two zones with different merge semantics:

- **`vault` zone** — shared authority. Layer 1 overlays Layer 0 via simple deep-merge. Layer 2 cannot override this zone (a warning is emitted if it tries). This ensures all machines agree on operator profiles, brain name, and other shared facts.
- **`defaults` zone** — customisable. Layer 1 overlays Layer 0, then Layer 2 overlays the result using type-based rules: scalars (local wins), booleans (either-true wins), lists (additive union). This allows local machines to extend feature flags or exclusion lists without replacing shared values.

## Consequences

- A vault with no config files at all works correctly — shipped defaults apply.
- Shared operator definitions are consistent across all machines working on the same vault.
- Local overrides (personal flags, excluded sync paths) never leak into committed config.
- The boolean "either-true wins" rule means enabling a feature locally or in the vault enables it everywhere that reads the merged config — appropriate for feature flags but a conscious trade-off.
