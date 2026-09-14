# Security Model

Brain-core applies a layered security model to all vault writes. The layers are:
path boundary enforcement, write-guard filtering, privilege-split profiles,
vault-scoped cross-process mutation serialization, and atomic writes.
Each layer is independent; all must pass for a write to succeed.

---

## Path Boundary Model

Every write path is resolved through `resolve_and_check_bounds(path, bounds)` before
any I/O begins.

**What it does:**

1. Calls `os.realpath()` to resolve the full chain of symlinks to a canonical
   filesystem path.
2. Compares the resolved path against the `bounds` directory — the vault root for
   vault writes. (Intermediate body files are a separate path: they are staged in
   the system temp directory and bounds-checked against it, not the vault root.)
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
| `_Assets/` | **blocked generally** | Only `attachment.upload` may create files in a validated scope beneath `_Assets/Attachments/` |
| `_Plugins/` | **blocked** | Plugin data, not agent-written content |
| `_Workspaces/` | **blocked** | Workspace config, not agent-written content |
| `_Temporal/` | **allowed** | User-facing temporary/in-progress artefacts |
| `_Config/` | **allowed** | User-facing configuration artefacts |

The model is additive: any new underscore-prefixed directory is blocked by default and
must be explicitly added to `_WRITE_ALLOWED_UNDERSCORE` in `_common/_filesystem.py` to become
writable.

Attachment upload is an operation-specific capability, not an allowlist entry.
Brain requires either a resolvable canonical living artefact key or a bare
validated standalone folder key, then constructs
`_Assets/Attachments/<scope>/<filename>` itself. It accepts no destination path,
rejects symlinks throughout the derived namespace, bounds-checks the target,
and uses the same atomic byte-write kernel. General create/edit access to
`_Assets/` and all writes to `_Assets/Generated/` remain blocked. See
[DD-059](decisions/dd-059-attachment-upload-boundary.md).

### Brain-core belt-and-suspenders

`check_not_in_brain_core(path, vault_root)` is an explicit final check that the
resolved path does not fall inside `.brain-core/`. This is belt-and-suspenders:
`check_write_allowed` already blocks `.brain-core` via the dot-prefix rule, but the
explicit check ensures correctness even if paths are constructed with unusual joining.

See: [DD-031: Path security model](decisions/dd-031-path-security-model.md)

---

## Privilege Split

Five cumulative built-in profiles define what each agent can do:

| Profile | Allowed tools |
|---|---|
| `reader` | Observe and discover; inspect and request authorisation within its permissions |
| `contributor` | Reader permissions plus ordinary content creation, editing and lifecycle work |
| `maintainer` | Contributor permissions plus definition, plugin and derived-index maintenance |
| `operator` | Maintainer permissions plus workspace registration and runtime operations |
| `administrator` | Operator permissions plus irreversible artefact deletion |

Profiles are defined in `defaults/config.yaml` under `vault.profiles` and can be
extended or replaced in `.brain/config.yaml`. The default profile when no key is
supplied is `operator` for single-operator local vaults.

**Per-command enforcement:** Credential permissions define the enduring maximum. Current authorisation is checked separately before dynamic resolution and again at guarded operation entry. New configurations initially authorise normal content work within that maximum; explicit read-only or exact command settings can narrow it. Exceptional operations require an explicit `access.request` through the caller's harness. Unknown identities, malformed policy and requests above the maximum fail closed. Custom profiles are preserved, including missing controls; configuration inspection identifies conflicts without widening permissions.

**Design intent:** A read-only summariser gets `reader`; an agent working normally
with content gets `contributor`; a Brain custodian gets `maintainer`; an agent managing
workspace/runtime operations gets `operator`; and only a principal trusted with
irreversible deletion gets `administrator`. The profiles live in the vault zone of
config, so they are shared across all machines and cannot be overridden locally.

See: [DD-033: Operator profiles](decisions/dd-033-operator-profiles.md)

### Authorisation and permission boundaries

`access.request` is a distinct tool so a harness can require manual approval for it or allow automatic execution. Brain does not assert that a human clicked approval. A request authorises one reviewed prepared operation or one exact command within the credential's existing permissions. Brain policy may deny requests entirely. There is no approval queue, timer, renewal or silent request-and-retry path.

Consent belongs to one Brain, principal and private MCP instance or explicit CLI job. The proxy retains its owner across compatible child replacement; a fresh proxy starts without exceptional consent. A job authenticates once, passes a private inherited channel, and closes admission when its root program ends. Children must explicitly preserve that descriptor across subprocess APIs that close inherited descriptors. No environment token or public context identifier substitutes for the channel. Pinned operator registration removal or rotation revokes the owner; current configuration is checked on subsequent use.

Permission changes, initial-policy changes and command/interface changes invalidate existing consent irreversibly. Restoring the old configuration cannot reactivate an observed invalidated grant. Permissions are changed only through the CLI-owned `permission.set-profile`, by an authenticated administrator, with revision-checked preview and audited update. It changes an existing registered operator's profile; it cannot change the anonymous default principal or invent access to a remote Brain.

MCP registers commands within current permissions. `command.list` and `command.describe` also disclose static metadata for above-permission commands without probing providers or resources. Current authorisation does not change tool registration; cached definitions never grant authority. Permission changes require fresh discovery/reconnection where the host caches registrations.

Legacy explicit initial-profile settings are preserved during migration. An old external-approval policy becomes `request_policy: migration_required` until an administrator deliberately selects the new policy. Unsupported legacy access settings are rejected, and custom-profile conflicts are reported for inspection.

---

## Operator Authentication

MCP accepts `BRAIN_OPERATOR_KEY` as trusted server configuration and CLI accepts
`--operator-key` as adapter input. Neither value is a semantic request field.
`authenticate_operator()` in `config.py`:

1. Hashes the supplied key with SHA-256, formatted as `sha256:<hexdigest>`.
2. Compares the hash against `vault.operators[]` entries in config.
3. Returns `(profile_name, operator_id)` on match; raises `ValueError` on mismatch.
4. If no key is supplied, returns the configured default profile with no operator id.

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

The same sibling-tempfile pattern is used directly in self-contained upgrade
and historical migration paths so those paths stay atomic without depending on
`_common` during early upgrade flows.

The new `repair.py` bootstrap path is deliberately narrower: it repairs or
creates the central managed runtime at `~/.brain/venvs/py<X.Y>-<sha16>/` and
then hands off into that runtime for packageful work. Brain installs its
dependencies into that shared local runtime, never into the user's wider
Python environment. Current-vault repair scopes such as `registry` are scoped
to machine-local files under the vault (`.brain/local/...`) and do not
broaden into user-home config or cross-vault registries by default. See
[DD-048: Central managed runtime](decisions/dd-048-central-managed-runtime.md).

Durable command outcomes are a separate fixed internal write capability under
`.brain/local/command-outcomes/`. Callers supply an opaque invocation ID, never
a path: the store hashes it to a fixed hexadecimal filename. It refuses
symlinked directory components and non-regular records, bounds atomic writes to
the selected Brain, serialises cross-process updates, and retains only command
identity, outcome state, timestamp and compact effect references. Request
bodies, credentials and provider values are not representable in the receipt
schema. A privacy-minimal compact index maps only hashed receipt filenames to
timestamps, so ordinary writes do not inventory every durable record. Index
publication is atomic, its endpoint must be a regular non-symlink file, and a
missing or malformed index is rebuilt from the authoritative receipt files;
explicit maintenance remains the full-inventory repair path. Proven no-effect
results are not persisted.

The DD-073 internal receipt foundation uses a separate owned namespace until
public cutover. Each invocation has immutable admission intent and a separate
immutable final outcome, retained as one unit in a globally bounded index.
Ownership includes Brain, authenticated principal and MCP/job context;
standalone CLI recovery has a separate Brain/principal namespace. Legacy
receipts cannot confer ownership. Intent precedes final guarded admission, and
failure to persist it prevents entry. Missing final outcomes remain unknown;
they never refund entered consent. Execution state is independent of effects:
an observation can succeed or become uncertain without mutating content.
Bodies, credentials and prepared review text are excluded from durable records.

The private owner channel is an inherited OS capability, not a public context
identifier or environment token. Entry points remove its locator and mark its
descriptor non-inheritable before executing providers; trusted CLI handoffs
forward it explicitly. Each replacement MCP child receives a fresh stream to
the same proxy-owned state. Closing the proxy or CLI job ends admission even
when descendants survive. A failed established channel never becomes a new
standalone context or permission to retry an uncertain invocation.

`invocation.read` is a strictly non-mutating lookup. Missing receipt storage
returns no receipt without creating directories or lock files; expired records
are logically absent without deletion. Atomic publication lets readers avoid a
cross-process write lock, while receipt writes and explicit maintenance retain
serialised retention cleanup.

Operational diagnostics are another fixed internal write capability, under
`.brain/local/diagnostics/` (0700 directory, 0600 files, per-family sidecar
locks). The stream is content-free by construction: its record schema has no
message field, rejects unknown events/fields, projects protocol methods into a
closed vocabulary, and reduces errors to a closed class vocabulary plus the
exception type name — request bodies, vault content, paths and exception text
are unrepresentable. Directory components reject symlinks; lock and log
endpoints use no-follow, same-regular-file verification before append,
truncation or export.
Callers pass typed records, never paths; storage is hard-capped per family by
rotation; writers never block or unwind a command. The only content-bearing
surface is the opt-in `BRAIN_LOG_BODIES` wire capture, which is confined to
its own `debug-bodies.log` family and flagged on the operational stream. See
[DD-067](decisions/dd-067-operational-diagnostics-logging.md) and
[Diagnostics](../functional/diagnostics.md).

The MCP proxy/server protocol marker is a local compatibility assertion, not an
authentication credential. The long-lived proxy sets it only in the child
environment; a replacement server with a missing, malformed or incompatible
marker completes initialisation but refuses every tool call before lookup or
effects. Proxy 0.6.0 then validates the catalogue-derived interface header and
owns the invocation identifier inserted into forwarded call metadata. Caller
metadata cannot replace that identifier at the proxy boundary. After unexpected
mutation loss the proxy queries only the fixed `invocation.read` command and
validates the returned reference, command identity/version, state, timestamp
and bounded effect records before reporting a known outcome. Any absent or
contradictory fact remains non-retryable and outcome-unknown; the proxy never
uses receipt absence as proof of no effect and never replays the mutation.

The staged direct `command.py` adapter keeps semantic request data separate
from trusted locality. `--vault` must resolve to a regular installed Brain with
non-symlinked Brain Core identity files. `--workspace` is accepted as invocation
context only after the existing read-only binding resolver proves that exact
workspace belongs to the selected Brain; request JSON cannot inject a caller
filesystem path. Profile authentication and granular allow-list evaluation run
before command execution. The adapter probes capabilities but never provisions
dependencies, changes runtimes or hands execution to another process.

Canonical orphan-runtime pruning is launcher-owned and fail-closed. It includes
the current Brain from trusted launcher context, compares derived machine state
without rewriting it, and removes only runtimes classified as unselected with a
successful live-process scan. A recursive-deletion error is outcome-unknown
rather than a retryable no-effect failure because part of the directory may
already be gone.

Canonical legacy Brain migration is also launcher-owned and requires the
machine-local caller-filesystem provider. It passes a typed selector to
read-only machine discovery, delegates repair only through version-matched
target-Brain processes and removes the legacy runtime only after repair and
live-process checks succeed. A child spawn failure proves no effect; timeout,
invalid child output or recursive-delete failure is outcome-unknown. A
child-reported partial result is retained as known partial with the affected
repair scope receipted.

Canonical managed-runtime repair is a bootstrap-tier launcher operation over a
Brain selected through trusted context. It does not accept a host path as
semantic request data and does not hand off into the managed runtime. An
existing unusable runtime is not automatically deleted or replaced because it
may still be live. Runtime creation or dependency-sync interruption is
outcome-unknown; a completed dependency mutation followed by failed verification
is known partial at the managed-runtime scope.

Client skill adapters are an explicit global or canonically resolved project
exception to ordinary vault write bounds. `skill.expose` writes only the named
skill beneath the selected client's `.claude/skills/` or `.codex/skills/`
directory. It
uses atomic writes with the skill directory as the bound, refuses symlinked
targets, and records an expected content hash in a Brain ownership marker.
Unmanaged or modified content is preserved; `--replace` archives an unmanaged
directory under the client-root `.brain-skill-backups/` directory before
installing, keeping executable skill discovery separate from recoverable data.
The backup root is also symlink-refused, and removal applies only to an
unmodified managed adapter. Vault upgrades never mutate these client-global
locations implicitly. See [DD-058](decisions/dd-058-active-brain-skill-adapters.md)
and [DD-068](decisions/dd-068-git-backed-skill-sources-and-managed-exposure.md).
The canonical launcher owner receives the home directory as trusted context,
not request data, and preflights every selected client through a no-write path
before applying the first change. Dry-run therefore exercises the real
ownership and destination checks without creating client directories.

Git-backed skill acquisition fetches into a temporary repository and extracts a
bounded `git archive`. It never checks out repository content, runs hooks, or
invokes configured checkout filters. Package validation rejects links and
special files, traversal and case-folding collisions, missing or mismatched root
metadata, and over-limit trees before installation.

The application boundary accepts only closed HTTPS, SSH URL, or SCP-style SSH
repository grammars and safe literal refs. HTTPS remotes cannot contain user
information; SSH identities use a bounded safe-character grammar. The boundary
rejects malformed authority escapes before Git invocation, along with local
paths, `file://` and Git remote-helper transports, query/fragment suffixes,
option-shaped refs and Git revision expressions. Git-backed status and mutation
commands declare their external Git provider and publish an open-world MCP
annotation; local-repository imports are not part of the remote application
surface.

**Exclusive mode:** `safe_write(exclusive=True)` (used by `artefact.create`) checks file
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
processes.

The launcher-owned MCP configuration flow supplies that higher-level fixed-file
coordination for its own bounded machine-local targets. It rejects symlinks in
each destination chain, strictly decodes every affected file before writing,
records its original bytes, checks again for concurrent change, then applies
sibling-temp atomic replacements. An application failure or process-level
interruption restores every written file and removes transaction-created empty
directories before the interruption is re-raised. If restoration cannot
be proven complete, the command returns a known-partial receipt naming each
surviving path. This transaction does not expand ordinary vault content write
permissions: it is limited to the trusted selected Brain, caller workspace and
user-home MCP configuration paths owned by `mcp.configure`/`mcp.repair`.
Existing fixed-file replacements retain the destination permission mode, so
self-replacing `brain.upgrade` does not turn the global CLI into a non-executable
data file.

Launcher-owned uninstall is constrained to the Brain selected in trusted
context. Before any cleanup it requires a regular `.brain-core/VERSION` and
rejects symlinks or non-directory values at `.brain-core/`, `.brain/` and
legacy `.venv/`. It removes only exact recorded project/local MCP entries and
the matching registry row before those three system directories. Vault notes,
shared managed runtimes, user-scope MCP state and the global CLI are never
recursive-deletion targets. A recursive failure is reported as unknown rather
than asserting a retry-safe partial deletion.

During MCP `session_start`, the non-critical session-mirror refresh is
dispatched to a single long-lived daemon worker via a `maxsize=1` coalescing
queue (see dd-036 "Session-mirror write path"). The request only enqueues, so a
stalled markdown-mirror write cannot block mandatory bootstrap; the single-worker
invariant also means two concurrent mirror writes can never interleave on
disk, and an `atexit` drain with a bounded cap lets any in-flight write
finish cleanly on normal shutdown. An orphaned `session.md.*.tmp` left
behind by a killed worker is swept when the MCP mirror worker is composed.
Direct CLI/script calls retain synchronous persistence, and both policies avoid
byte-identical rewrites.

`upgrade.py` carries its own self-contained sibling-temp write helper because it
cannot import `_common` while replacing `.brain-core/` in place. Its rollback
snapshot path now stores raw bytes and restores them through the same temp +
fsync + replace shape, so binary or non-UTF-8 files under `.brain/` and
`_Config/` do not break pre-compile rollback. Post-compile migrations also
snapshot the affected artefact roots before mutating them. A migration that may
write any additional root, project or machine file declares each exact path
through `prospective_effects()` before its first write; upgrade snapshots those
paths and verifies their restoration alongside vault content and `.brain-core/`.
Rollback records both file bytes and the original directory topology, attempts
every independent restoration, and retains original bytes plus exact recovery
paths when any file or introduced directory cannot be restored.
The coordinated CLI replacement commits once the new binary/distribution pair
is verified. Failure to remove an old backup is post-commit recovery work, not a
reason to roll Brain Core back underneath the installed CLI.

`rename.py` now also fails closed on unsafe move sets before any wikilink
rewrite begins: existing-destination collisions, duplicate/cyclic batch moves,
symlink endpoints, and destination parent components that are files or broken
symlinks all raise during preflight. That keeps a path-valid but unsafe rename
or ownership move plan from clobbering an existing artefact or rewriting links
to a path that will not be created.

Application mutation owners refuse stale or unreadable compiled state before
writing. MCP, CLI, direct `command.py` and typed Python therefore share the same
precondition: stale derived state is repaired through an explicit command or
surfaced as an actionable error, never used to drive a mutation.

Document mutation owners also require the SHA-256 revision of the exact bytes
returned by the preceding editable read. They compare it while holding the
vault mutation lock immediately before effects; a mismatch consumes no staged
content and returns a conflict with re-read guidance. This prevents lost updates
across agents, humans, and adapters without relying on timestamps.

See: [DD-036: Safe write pattern](decisions/dd-036-safe-write-pattern.md)

---

## MCP Mutation Serialization

Mutating selected-Brain commands across MCP, CLI, direct script and typed Python
are serialised behind a vault-scoped cross-process lock. This includes granular
artefact/content/definition mutations, attachment upload, shaping/rendering,
link repair and runtime/index mutations.

**Why it exists:** some script paths that look single-file can trigger broader
vault mutations, such as status-driven moves and vault-wide wikilink rewrites.
Serializing mutations prevents these flows from interleaving across agents,
MCP servers, and CLI processes sharing one vault.

The canonical lock lives below the application boundary, with bounded
acquisition and owner diagnostics. It coordinates processes sharing one vault;
machine-global launcher transactions use their own fixed-state locking and
checked rollback boundaries.

Mutation outcome classification also occurs while that lock is held. A
post-effect failure whose commit state cannot be proven is reported as
`command_outcome_unknown`; public command adapters keep stderr bounded and send
full exception diagnostics through the trusted diagnostic sink with correlation
metadata once trusted invocation context exists.

---

## Cross-references

- [DD-031: Path security model](decisions/dd-031-path-security-model.md)
- [DD-033: Operator profiles](decisions/dd-033-operator-profiles.md)
- [DD-036: Safe write pattern](decisions/dd-036-safe-write-pattern.md)
- [MCP tools — tool permission recommendations](../functional/mcp-tools.md)

## MCP invocation ownership

Proxy protocol 3 requires a validated child command-interface header before
forwarding any tool call. The proxy rejects caller-supplied `brainInvocation`
metadata and records each accepted invocation itself. Legacy initialization
and modern request-scoped discovery establish the same contract; replacement
re-establishes it before replay or receipt lookup. Receipt queries also carry
a new proxy-owned invocation identity in the negotiated protocol era.
