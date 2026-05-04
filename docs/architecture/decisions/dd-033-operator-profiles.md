# DD-033: Operator profiles

**Status:** Implemented
**Extended by:** DD-045, DD-046

## Context

Different agents connecting to the MCP server need different levels of access. A read-only summariser should not be able to create or delete artefacts. A full operator agent needs access to all tools including `brain_move`, the residual `brain_action` workflow bucket, and the experimental `brain_process` surface when enabled. Without a permission model, every connected agent has full access regardless of intent.

The constraint is that MCP authentication is caller-supplied — the server receives whatever key the agent passes in `brain_session`. This means authentication must be verified, not assumed.

## Decision

Three built-in profiles are shipped with brain-core:

| Profile | Allowed tools |
|---|---|
| `reader` | `brain_session`, `brain_read`, `brain_search`, `brain_list` |
| `contributor` | All reader tools + `brain_create`, `brain_edit`, and `brain_process` |
| `operator` | All contributor tools + `brain_move`, `brain_action` |

Profiles are defined in `defaults/config.yaml` under `vault.profiles` and can be extended or replaced in `.brain/config.yaml`. The default profile (when no key is supplied) is `operator`, which preserves backward compatibility for single-agent vaults.

Operator authentication uses SHA-256 hashed keys stored in `vault.operators[]`. When `brain_session` is called with an `operator_key`, `authenticate_operator()` hashes the key (`sha256:<hexdigest>`) and matches it against stored hashes. No plaintext keys are stored in config. The `generate_key.py` script wraps `hash_key()` for operators who need to generate and register a new key.

Profile enforcement happens at the start of every tool call via `_enforce_profile()`. If the session profile does not include the tool in its `allow` list, the call returns an error immediately. No session state is carried between calls beyond `_session_profile` (set during `brain_session`).

## Consequences

- Multi-agent vaults can grant fine-grained access: a summariser gets `reader`, a writing agent gets `contributor`, an admin agent gets `operator`.
- SHA-256 hashing means a compromised config file does not expose raw keys.
- The default of `operator` for unauthenticated callers means existing single-agent setups require no migration.
- Profile definitions live in the vault zone of config, so they are shared across all machines and cannot be overridden locally.
