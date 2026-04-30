# Security Model

Brain-core applies a layered security model to all vault writes. The layers are:
path boundary enforcement, write-guard filtering, privilege-split profiles,
process-local mutation serialization in the MCP wrapper, and atomic writes.
Each layer is independent; all must pass for a write to succeed.

---

## Path Boundary Model

Every write path is resolved through `resolve_and_check_bounds(path, bounds)` before
any I/O begins.

**What it does:**

1. Calls `os.realpath()` to resolve the full chain of symlinks to a canonical
   filesystem path.
2. Compares the resolved path against the `bounds` directory (the vault root for
   vault writes; `/tmp` is permitted additionally for intermediate body files).
3. Appends `os.sep` before the prefix test so `/vault` never accidentally matches
   `/vault-other`.
4. Raises `ValueError` if the resolved path falls outside bounds, or if
   `follow_symlinks=False` and the path is a symlink.

**Why it matters:** An agent-supplied path such as `../../etc/passwd` or a symlink
pointing outside the vault would otherwise silently escape the vault tree. Resolving
symlinks before any path test closes that escape route.

The bounds check is the first thing `safe_write()` does, so no I/O ever starts for
an out-of-bounds path.

See: [DD-031: Path security model](decisions/dd-031-path-security-model.md)

---

## Write Guards

After bounds checking, `check_write_allowed(rel_path)` and
`check_not_in_brain_core(path, vault_root)` filter which vault locations are writable.

### Dot-prefix rejection

Any top-level folder whose name starts with `.` is unconditionally blocked:

- `.obsidian/` — Obsidian application config
- `.brain-core/` — the running brain-core scripts
- `.brain/` — vault-zone config and indexes
- Any other dot-prefixed system directory

This is a default-deny rule: new dot-prefixed directories are automatically protected
without requiring a blocklist update.

### Underscore-folder allowlist

Top-level folders starting with `_` are blocked unless they appear in the explicit
allowlist `{_Temporal, _Config}`:

| Folder | Status | Reason |
|---|---|---|
| `_Archive/` | **blocked** | Managed via `archive` action only |
| `_Assets/` | **blocked** | Static assets, not agent-written content |
| `_Plugins/` | **blocked** | Plugin data, not agent-written content |
| `_Workspaces/` | **blocked** | Workspace config, not agent-written content |
| `_Temporal/` | **allowed** | User-facing temporary/in-progress artefacts |
| `_Config/` | **allowed** | User-facing configuration artefacts |

The model is additive: any new underscore-prefixed directory is blocked by default and
must be explicitly added to `_WRITE_ALLOWED_UNDERSCORE` in `_common/_filesystem.py` to become
writable.

### Brain-core belt-and-suspenders

`check_not_in_brain_core(path, vault_root)` is an explicit final check that the
resolved path does not fall inside `.brain-core/`. This is belt-and-suspenders:
`check_write_allowed` already blocks `.brain-core` via the dot-prefix rule, but the
explicit check ensures correctness even if paths are constructed with unusual joining.

See: [DD-031: Path security model](decisions/dd-031-path-security-model.md)

---

## Privilege Split

Three built-in profiles define what each agent can do:

| Profile | Allowed tools |
|---|---|
| `reader` | `brain_session`, `brain_read`, `brain_search`, `brain_list` |
| `contributor` | All reader tools + `brain_create`, `brain_edit` |
| `operator` | All contributor tools + `brain_move`, `brain_action` |

Profiles are defined in `defaults/config.yaml` under `vault.profiles` and can be
extended or replaced in `.brain/config.yaml`. The default profile when no key is
supplied is `operator`, which preserves backward compatibility for single-agent vaults.

**Per-tool enforcement:** Profile enforcement happens at the start of every tool call
via `_enforce_profile()`. If the session profile does not include the tool in its
`allow` list, the call returns an error immediately. No state is carried between calls
beyond `_session_profile` (set during `brain_session`).

**Design intent:** A read-only summariser agent gets `reader`; a writing agent gets
`contributor`; an admin agent gets `operator`. The profiles live in the vault zone of
config, so they are shared across all machines and cannot be overridden locally.

See: [DD-033: Operator profiles](decisions/dd-033-operator-profiles.md)

---

## Operator Authentication

`brain_session` is the authentication entry point. When called with an `operator_key`,
`authenticate_operator()` in `config.py`:

1. Hashes the supplied key with SHA-256, formatted as `sha256:<hexdigest>`.
2. Compares the hash against `vault.operators[]` entries in config.
3. Returns `(profile_name, operator_id)` on match; raises `ValueError` on mismatch.
4. If no key is supplied, returns the default profile (typically `operator`) with no
   operator id — preserving single-agent backward compatibility.

**Key generation:** The `generate_key.py` script wraps `hash_key()` for operators who
need to create and register a new key. It prints the plaintext key (shown once, to be
passed to the agent) and the `sha256:` hash (to be stored in config). No plaintext
keys are ever stored.

**Config registration example:**

```yaml
vault:
  operators:
    - id: my-agent
      key_hash: "sha256:<hexdigest>"
      profile: contributor
```

SHA-256 hashing means a compromised config file does not expose raw keys.

See: [DD-033: Operator profiles](decisions/dd-033-operator-profiles.md)

---

## Safe Write Pattern

Vault writes use a shared atomic-write kernel exposed through
`safe_write(path, content, *, bounds, ...)` and
`safe_write_via(path, writer, *, bounds, ...)`, which implements the
tmp-fsync-rename pattern:

1. **Bounds check** — `resolve_and_check_bounds()` runs first; no I/O begins for
   out-of-bounds paths.
2. **Write to a unique sibling temp file** — Content is written to a fresh
   `mkstemp()` path in the same directory as the target. Keeping the temp file on
   the same filesystem guarantees that `os.replace()` is a single `rename(2)`
   syscall — atomic on POSIX.
3. **`f.flush()` + `os.fsync(f.fileno())`** — Flushes the OS page cache to stable
   storage before the rename, so a crash after the rename cannot produce an empty file.
4. **`os.replace(tmp, target)`** — Atomically replaces the target. The old content
   remains intact until the rename completes. If the rename fails, the original file
   is untouched.
5. **Cleanup on any exception** — A `BaseException` handler unlinks the tmp file if
   any step fails, preventing orphan temp files in the vault.

**Unique temp names:** Because each call gets its own sibling temp path, two
threads in the same process no longer collide on a shared temp filename when they
target the same file.

The same sibling-tempfile pattern is used directly in the self-contained
bootstrap scripts (`init.py`, `upgrade.py`) and the historical migrations so
those paths stay atomic without depending on `_common` during early install or
upgrade flows.

The new `repair.py` bootstrap path is deliberately narrower: it repairs or
creates the vault-local `.venv` and then hands off into that managed runtime
for packageful work. Current-vault repair scopes such as `registry` are scoped
to machine-local files under the vault (`.brain/local/...`) and do not broaden
into user-home config or cross-vault registries by default.

**Exclusive mode:** `safe_write(exclusive=True)` (used by `brain_create`) checks file
existence before writing, providing a lightweight create-or-fail guarantee.

**`safe_write_json()`** is a thin wrapper over the same kernel, and
callback-driven serializers can use `safe_write_via()` without bypassing the
atomic replacement path.

**Guarantees provided:** A crash at any point leaves either the complete old content or
the complete new content on disk — never a partial write. This is essential because
Obsidian has no crash recovery, and a corrupted artefact may not be noticed immediately.

**What it does not guarantee:** `safe_write()` is an atomic replacement primitive,
not a transaction manager. If two independent callers both read-modify-write the
same target concurrently, the later replace still wins. Higher-level coordination
is required for multi-file rewrite flows and concurrent writers in multiple
processes. During MCP startup, the non-critical session-mirror refresh is
dispatched to a single long-lived daemon worker via a `maxsize=1` coalescing
queue (see dd-036 "Session-mirror write path"). Startup only enqueues, so a
stalled markdown-mirror write cannot block readiness; the single-worker
invariant also means two concurrent mirror writes can never interleave on
disk, and an `atexit` drain with a bounded cap lets any in-flight write
finish cleanly on normal shutdown. An orphaned `session.md.*.tmp` left
behind by a killed worker is swept on the next startup.

`upgrade.py` carries its own self-contained sibling-temp write helper because it
cannot import `_common` while replacing `.brain-core/` in place. Its rollback
snapshot path now stores raw bytes and restores them through the same temp +
fsync + replace shape, so binary or non-UTF-8 files under `.brain/` and
`_Config/` do not break pre-compile rollback. Post-compile migrations also
snapshot the affected artefact roots before mutating them, so a failed migration
restores both vault content and `.brain-core/` instead of leaving a half-moved
artefact tree behind.

`rename.py` now also fails closed on destination collisions: if the target path
already exists, it raises before any wikilink rewrite begins. That keeps a
path-valid but unsafe rename plan from clobbering an existing artefact.

See: [DD-036: Safe write pattern](decisions/dd-036-safe-write-pattern.md)

---

## MCP Mutation Serialization

Within the MCP server process, mutating tool calls are serialized behind a
process-local lock. This applies to:

- `brain_create`
- `brain_edit`
- `brain_move`
- `brain_action`

**Why it exists:** some script paths that look single-file can trigger broader
vault mutations, such as status-driven moves and vault-wide wikilink rewrites.
Serializing mutating MCP calls prevents those flows from interleaving inside one
shared server process.

**Why it lives in the MCP layer:** the script layer remains the source of truth
for vault behavior. The lock is runtime orchestration policy, so it belongs in
the wrapper rather than in the domain scripts themselves.

**Scope limit:** this protects one MCP server process only. Direct script users
and multi-process callers still need their own coordination if they perform
parallel writes against the same vault.

---

## Cross-references

- [DD-031: Path security model](decisions/dd-031-path-security-model.md)
- [DD-033: Operator profiles](decisions/dd-033-operator-profiles.md)
- [DD-036: Safe write pattern](decisions/dd-036-safe-write-pattern.md)
- [MCP tools — tool permission recommendations](../functional/mcp-tools.md)
