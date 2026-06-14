# DD-048: Central managed runtime, content-addressed by requirements

**Status:** Accepted (v0.37.0)
**Supersedes:** DD-043 (in part — see below)
**Extended by:** DD-049, DD-054
**Lookup clarification (v0.38.0):** creation remains keyed strictly by `(python_minor, requirements_hash)` — a brand-new install lands at the exact-match path. Lookup is softer: when the exact-tag path is missing, `find_existing_central_venv` returns any other central venv for the same `requirements_hash` whose minor is >= 3.12 (highest compatible minor wins). This keeps Brain useful across Python-minor churn (Homebrew bumping `python3.12` → `python3.13` is the common trigger) without re-provisioning. Pre-3.12 venvs are never picked. The broader "launcher selection duplicated across entry points" follow-up remains open — direct script entry points (`upgrade.py`, `init.py`) can still pick different launchers, which only matters during creation now that lookup is unified.
**Entry-point convergence (v0.39.0):** all managed-runtime entry points — the `brain` CLI dispatch helper, `repair.py`, `configure.py`, `upgrade.py` — now delegate runtime resolution to a single owner, `_common._venv.resolve_or_provision_central_venv`. The owner runs the same five-step decision for every caller: (1) try the exact-tag path; (2) try any compatible-minor (>= 3.12) venv for the same requirements hash; (3) probe the chosen runtime for compatibility and required modules; (4) if modules are missing, sync them in place against *that* runtime (pip install + sentinel update); (5) only create a new exact-tag venv when no compatible same-hash runtime exists. Before this convergence, each entry point resolved an exact-tag path of its own choosing and could create a parallel runtime after Python minor churn even though a compatible one already served the vault.

Creation semantics are unchanged: `ensure_central_venv` still lands new venvs strictly at `(python_tag(launcher), requirements_hash)`. The launcher-selection helpers themselves remain duplicated across entry points (each has its own `find_python_*` / `find_repair_launcher` / `find_launcher_python`), but every entry point now feeds its selected launcher into the same orchestrator, so the user-visible duplicate-runtime risk is closed. Cleanup of orphaned older-minor venvs (the deferred "future `repair.py orphan-runtimes`" item) stays a follow-up — `resolve_or_provision_central_venv` makes orphans useful in the meantime.

## Context

DD-043 made the vault-local `.venv/` the canonical Brain managed runtime: every vault carried its own copy of the Python interpreter and `mcp` plus other Python dependencies under `<vault>/.venv/`. That decision was correct for the launcher-vs-managed-runtime split it introduced; it was wrong for the *location* of the managed runtime.

Empirical investigation in `v0.35.9` (`.brain/local/mcp-proxy.log` against an iCloud-hosted vault) showed the cost. Across 765 proxy sessions:

- 85 (~11%) sent an `initialize` request, never received a response, and were SIGKILLed by Claude after its 30-second connection timeout.
- The slowest *successful* initialize sat at exactly 29.0 seconds — at the cliff edge.
- The slow path is **before** the server logs anything: ~29 seconds elapse between the proxy spawning the Python child and the server's first `startup begin` log line. That is pure Python interpreter + import time. With `<vault>/.venv/` in iCloud, every cold start triggers iCloud "fault in from cloud" reads for thousands of `site-packages/` files.

The full data is in `_Temporal/Reports/2026-05/20260508-report~MCP Cold-Start Investigation iCloud Materialisation Of Vault-Local Venv.md` (vault).

The user-facing failure mode — "MCP doesn't connect" — was structural: the runtime was placed where iCloud could evict it.

## Decision

The Brain managed runtime moves to a single machine-local location, content-addressed by the dependency manifest:

```
~/.brain/venvs/py<MAJOR>.<MINOR>-<sha256(requirements.txt)[:16]>/
```

Two consequences fall out of the keying:

- **Multiple vaults sharing dependencies share one venv.** The hash is content-addressed; install once, reuse N times.
- **Different requirements get different venvs.** When a brain-core upgrade changes `requirements.txt`, the new hash routes to a new venv; the old one stays for any vaults still on the previous version. Cleanup is a separate, deferred concern.

The Python minor version is part of the directory name because venvs are not portable across minor versions. This lets `python3.12` and `python3.13` users on the same machine coexist without colliding.

The path rule itself lives in exactly one place: `src/brain-core/scripts/_common/_venv.py`. Every caller — `install.sh`, `init.py`, `upgrade.py`, `repair.py` — resolves the path through that helper. The bash installer uses the helper's CLI mode (`python _venv.py path|python|ensure --vault X`) to avoid duplicating the rule in shell.

The launcher-vs-managed-runtime split from DD-043 stays intact. The bootstrap layer still starts from any compatible Python 3.12+; what changes is *where* the bootstrap converges to. Steady-state Brain execution lives in `~/.brain/venvs/<key>/`, not `<vault>/.venv/`.

The repo's own `.venv/` for development (used by `make test`) is unrelated and unchanged.

## Migration

Existing vaults with `<vault>/.venv/` keep working — `init.py` falls back to the legacy path when the central venv is absent and no central path is recorded. The migration from legacy is one command:

```bash
python3.12 .brain-core/scripts/init.py --vault . --project . --client all
```

When run on a vault that has the new `_venv.py` helper, `init.py` prefers the central venv. If the central venv does not exist yet, `upgrade.py` (run during a regular brain-core upgrade) creates it; the user then re-runs `init.py` to point MCP config at the new path. The legacy `<vault>/.venv/` may then be deleted; it is no longer referenced.

`upgrade.py`'s post-upgrade output flags vaults that still have a legacy `.venv/` and prints the exact migration command. No silent rewrites of MCP configuration files happen during upgrade.

## Alternatives considered

### 1. Per-vault `.venv/` with `.nosync` rename (or equivalent per-cloud-provider exclusion)

Rejected. Each cloud provider exposes a different exclusion mechanism (`.nosync` on iCloud, `.dropbox-cache/` patterns, Drive's exclusions, OneDrive's `.foo` flags). The Brain installer would need to know which provider hosts every vault. The exclusion can also be reversed by the user without Brain noticing. Moving the runtime out of any synced location is portable across providers and not silently reversible.

### 2. One venv per brain-core *version*

Rejected. Brain-core ships docs, scripts, and dependency manifests; only the manifest needs to drive runtime identity. Between releases that don't touch `requirements.txt`, a version-keyed scheme would create a fresh venv unnecessarily — wasting disk and re-running pip for no benefit. Content-addressing skips that work.

### 3. Centralise brain-core code (`scripts/`, `brain_mcp/`, `artefact-library/`) too

Deferred. The scripts and `brain_mcp/` package together are a few MB; their cold-import cost on iCloud is dominated by the venv (`numpy`, `sentence-transformers`, etc) by orders of magnitude. Moving them is a larger refactor that re-shapes how vaults relate to brain-core releases. The setup-bootstrap design will weigh that separately. For now, only the venv moves.

## Consequences

- Cold-start MCP initialise is no longer dominated by iCloud materialisation; the runtime is on local disk and Python imports run at native speed.
- Multiple vaults that share a brain-core release also share its venv on disk. A typical user with one or two vaults stops paying duplicate `~200 MB` of `site-packages/`.
- The repair-launcher contract from DD-043 is unchanged: external Python 3.12+ may bootstrap the central runtime, just as it bootstrapped `<vault>/.venv/` before.
- DD-043's "vault-local `.venv` is canonical" claim is superseded; the launcher-vs-managed-runtime split it formalised is preserved.
- Legacy `<vault>/.venv/` directories are left in place when vaults migrate. Users may delete them once MCP config is repointed; the next `bash install.sh --uninstall` flow still cleans them up if present.
- A future `repair.py orphan-runtimes` (or equivalent) is needed to clean unreferenced venvs at `~/.brain/venvs/`; tracked as deferred work, not blocking this DD.
