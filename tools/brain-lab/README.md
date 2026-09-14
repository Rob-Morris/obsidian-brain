# Disposable Brain Linux Environments

`brain-lab` is a contributor-only Docker control plane for reproducible Brain installation, upgrade, diagnosis, and user-vault reproduction. It creates immutable inputs and verified baselines, then exposes disposable writable runs to both its own primitive operations and ordinary Docker commands.

It does not ship in Brain Core or the template vault. A lab is a Linux container userland sharing Docker Desktop's kernel boundary; it is not a full virtual machine and does not test systemd, desktop Obsidian, a separate kernel, or native Linux GUI behaviour.

## Safety boundary

- Source worktrees and imported vaults are streamed into immutable local Docker images. They are never writable-mounted into containers.
- The Docker socket is never mounted inside a run.
- Every managed Docker resource carries `io.github.rob-morris.brain-lab.*` labels. Destruction verifies the exact recorded ID and labels; the tool never invokes Docker prune.
- Imported vaults are visibly labelled and copied into Docker Desktop's local storage. There is no push or publish operation, but this is not encryption or secure erasure.
- Imported seed deletion requires `confirm_imported: true` and remains manual.
- Runs default to Docker network `none`; request `bridge` explicitly when a test needs network access.

Docker control is powerful: a host process with access to the Docker socket can start privileged containers or request host mounts even though brain-lab itself does neither.

### Docker configuration and registry credentials

Brain Lab resolves Docker's selected context and daemon identity before replacing
the client configuration. `DOCKER_CONTEXT` or `DOCKER_HOST` overrides the
configured current context. If both variables are non-empty, Brain Lab rejects
the selection as ambiguous; installed Docker CLI versions differ in how that
combination is resolved (CLI 29.7.2 selects the host). The
resolved Unix socket is pinned for the operation and its daemon identity is
checked before each command, including interactive shells. Remote transports
and TLS environment settings are currently unsupported and fail explicitly;
there is no fallback to `/var/run/docker.sock`.

Public workflows use a private temporary `DOCKER_CONFIG` with no credentials,
credential helpers, inherited plugin configuration or inherited builder selection. Buildx itself may execute. A harmless empty
`auths` entry for `brain-lab.invalid` prevents Docker's automatic native
credential-store discovery (an entirely empty config does not). Temporary
configuration files are removed after each command. Discovery reads Docker's
existing endpoint configuration but does not request registry credentials.

Private registry pulls and builds require explicit opt-in:

```sh
tools/brain-lab/brain-lab --registry-auth-config /path/to/registry-config.json \
  --json base build --request-json \
  '{"image":"private.example/ubuntu:24.04","platform":"linux/arm64"}'
```

The supplied file must contain only a non-empty `auths` object with inline Docker
credentials. Each registry selects exactly one mode: strict base64 `auth`
encoding a non-empty `username:secret`, a non-empty `identitytoken` or
`registrytoken`, or a complete non-empty `username`/`password` pair. Empty,
incomplete, malformed and mixed modes fail before invoking Docker.
Helper-backed configurations (`credsStore`, `credHelpers`) are
rejected. Credentials are copied only into the temporary configuration used by
pull/build; other commands remain in public mode. Authenticated command output
is discarded before evidence persistence, because a credential may be echoed in
an unknown encoding. Receipts mark `output_redacted: true` and evidence as
`partial`; credentials and their configuration file path are not recorded.

Each daemon command records `docker-connection.json` containing the resolved
endpoint, daemon identity, starting context, CLI/server versions, credential
mode and the names of relevant starting environment variables. Values other
than `DOCKER_HOST` and `DOCKER_CONTEXT` are redacted. Nested scenario steps
share one endpoint/daemon pin until the outer dispatch completes. Interactive
shells retain their connection document and command/return-code receipt in the
operation bundle; their uncaptured interactive output makes evidence partial.
Discovery failures, unsupported
or ambiguous endpoint settings, daemon changes and invalid credential
configuration return typed errors. Endpoint discovery diagnostics are kept in
temporary storage and removed rather than copied into evidence.

## Prerequisites

- Python 3.12 or newer on the host
- Git
- Docker Desktop or a compatible Docker Engine with the `docker` CLI available
- enough Docker and host storage for the selected source and vault copies

Ubuntu 24.04 on explicit `linux/arm64` is the primary full-workflow platform. `linux/amd64` is an opt-in emulation smoke when the local engine supports it. Supported Brain versions and their exact commands are declared in `compatibility.json`; versions outside those ranges fail before preparation.

## Resource graph

```text
base + source + seed + preparation adapter
                  |
                  +--> attempt --> verified baseline --> disposable run
                                                       |
                                                       +--> evidence/results
```

- **base** — pinned Ubuntu image, prerequisites, and isolated non-root `brain` user
- **source** — immutable Git commit or captured worktree
- **seed** — matching source template or coherently captured imported vault
- **attempt** — unpublished preparation state, retained on failure
- **baseline** — immutable image promoted only after required health gates pass
- **run** — writable container recreated from an unchanged base or baseline
- **result** — operation receipt referring to an immutable evidence directory

Changing a result directory, scenario, run network, timestamp, or arbitrary command does not change a baseline identity. Changing its platform/base, source tree, seed tree, preparation mode, compatibility adapter revision, or schema does.

Each base image records the exact APT repository configuration and installed package versions at `/usr/local/share/brain-lab/package-inputs.json`. The base receipt points to that file and the immutable image ID that content-addresses it; the resolved Ubuntu image digest remains part of the base identity.

## Invocation contract

The executable accepts one resource noun, one verb, and one JSON request object:

```sh
tools/brain-lab/brain-lab [--state-dir PATH] [--json] RESOURCE VERB \
  --request-json 'JSON'
```

Use `--request-json -` to read a multiline request from standard input. `--json` emits `brain-lab.operation-result/1`; human output is a concise rendering of the same envelope. Non-interactive operations have bounded timeouts and streamed, bounded stdout/stderr evidence. The default per-stream retention limit is 8 MiB and can be overridden with `--stream-limit`.

The default state directory is `${XDG_DATA_HOME:-~/.local/share}/brain-lab`. Repository acceptance uses `.brain-lab/`, which is gitignored; override it with `BRAIN_LAB_STATE_DIR` or `--state-dir`.

Every result reports these independently:

- `outcome`: `success`, `failure`, `timeout`, `cancelled`, or `unknown`
- `effect_certainty`: `none`, `committed`, `partial`, or `unknown`
- `evidence_completeness`: `complete` or `partial`

A failed preparation returns its attempt and retained container IDs. A failed scenario retains the nested primitive result envelopes completed before it stopped.

## Prepare a current-worktree template baseline

Build the explicit platform base:

```sh
tools/brain-lab/brain-lab --json base build --request-json \
  '{"image":"ubuntu:24.04","platform":"linux/arm64","timeout_seconds":1800}'
```

Capture tracked files and tracked modifications from the worktree. Untracked files are excluded unless each reviewed path is named in `include_untracked`:

```sh
tools/brain-lab/brain-lab --json source capture --request-json \
  '{"kind":"worktree","path":".","platform":"linux/arm64","include_untracked":[]}'
```

Use the returned source ID to derive its matching template seed:

```sh
tools/brain-lab/brain-lab --json seed template --request-json \
  '{"source_id":"source-…"}'
```

Prepare the verified baseline using returned IDs:

```sh
tools/brain-lab/brain-lab --json baseline prepare --request-json \
  '{"base_id":"base-…","source_id":"source-…","seed_id":"seed-…","mode":"template","timeout_seconds":1800}'
```

Preparation uses the selected source's official installer. It checks Core version, CLI/machine health, `brain session start --json`, a direct MCP stdio read-only round trip, and template compliance. A retry occurs only when the selected compatibility manifest explicitly permits a known safe, retryable health response such as cold runtime warm-up.

## Remote and historical sources

Resolve or capture an exact Git ref:

```sh
tools/brain-lab/brain-lab --json source resolve --request-json \
  '{"repository":"https://github.com/rob-morris/obsidian-brain.git","ref":"v0.54.0"}'

tools/brain-lab/brain-lab --json source capture --request-json \
  '{"kind":"git","repository":"https://github.com/rob-morris/obsidian-brain.git","ref":"v0.54.0","platform":"linux/arm64"}'
```

The ref is resolved and checked out to an immutable commit before capture. The selected historical source owns its installer, post-install compatibility pins, generated template preparation, rehydration, and health commands through the versioned adapter. For Brain 0.51–0.54 the adapter pins MCP 1.29.0 because those releases declared an unbounded `mcp>=1.0.0` dependency and their MCP 1.x server cannot run against MCP 2.x.

## Imported vaults

Import copies a coherent vault into local Docker storage and records before, staged, and after manifests:

```sh
tools/brain-lab/brain-lab --json seed import --request-json \
  '{"path":"/absolute/path/to/copied-vault","platform":"linux/arm64"}'
```

Preparation requires both `.brain-core/VERSION` and the normalised Core tree to match the selected source exactly.
Normalisation excludes generated caches and the distribution-only `scripts/upgrade.py` bootstrap, which the
canonical upgrade path deliberately does not converge into an installed vault; every installed runtime file
remains content-exact.

- `preserve` installs matching machine state against a disposable template, replaces it with the immutable seed, and performs no repair of the copied vault. Operational failures are evidence, not an invitation to mutate it.
- `rehydrate` additionally runs only the selected adapter's verified machine/generated-state reconstruction. Imported `.brain/local/`, `.codex/`, and `.claude/` state is cleared, and only the imported `brain` server entry is removed from `.mcp.json`; unrelated MCP servers are preserved. It rejects any change to portable vault content or Brain Core and never uses forced migration replay as reconstruction.
  Adapters restore only their declared portable side effects from the immutable seed before the full-tree equality gate; unexpected portable changes still fail preparation.

```sh
tools/brain-lab/brain-lab --json baseline prepare --request-json \
  '{"base_id":"base-…","source_id":"source-…","seed_id":"seed-…","mode":"preserve"}'
```

Treat real user-vault acceptance as local and manual. Export only a redacted receipt; do not commit raw evidence containing vault content.

## Runs and direct Docker interoperability

Start a run from a verified baseline:

```sh
tools/brain-lab/brain-lab --json run start --request-json \
  '{"baseline_id":"baseline-…","network":"none"}'
```

Use `"network":"bridge"` explicitly for installers or tests that must fetch dependencies. A no-network run is intentionally unsuitable for those operations.

Copy either a reviewed host path or an immutable captured source resource into a run. The latter makes upgrade workflows reproducible without an out-of-band Docker extraction. The destination must be a new path beneath `/home/brain` whose parent already exists. Brain Lab then normalises only that new path to the container's `brain` user without following copied symlinks; it never recursively changes an existing destination directory:

```sh
tools/brain-lab/brain-lab --json run copy-in --request-json \
  '{"id":"run-…","source_id":"source-…","destination":"/home/brain/target-source"}'
```

The result includes the raw container ID and suggested `docker exec`, `docker inspect`, and `docker cp` commands. Run an argv-safe command:

```sh
tools/brain-lab/brain-lab --json run exec --request-json \
  '{"id":"run-…","argv":["brain","session","start","--request-json","{}","--json"],"working_directory":"/home/brain/vault","environment":{"BRAIN_VAULT_ROOT":"/home/brain/vault"},"timeout_seconds":300}'
```

Every `run exec` records bounded command streams plus before/after run-filesystem manifests, including generated vault-local state while excluding declared transient command evidence. Internal manifest transport is deterministically compressed. Its `filesystem_change` payload references `filesystem-diff.json`, which contains complete added/removed/changed path metadata and bounded UTF-8 content evidence for added or changed files. Evidence failure marks the operation evidence partial without replacing the command's primary outcome.

An historical-to-target upgrade is ordinary composition: start the historical baseline with bridge networking, copy in the target `source_id`, run that source's official installer, then run the target adapter's declared rehydration and health commands from `compatibility.json`. `run recreate` remains the authoritative reset to the unchanged historical baseline.

The checked-in `scenarios/historical-upgrade.json` gate automates that path from the exact v0.53.5 product-comparison commit. It installs the target CLI distribution, proves that production `brain upgrade --dry-run` previews the Core replacement and retained migrations without changing portable vault state, then performs the effectful upgrade. The gate migrates representative built-in and custom profiles plus shipped bootstrap instructions, preserves user-authored prose and content, removes retired Core skills, adds one controlled inherited finding and one keyless terminal-status record, and fails on any unexplained post-upgrade finding identity. It also requires the migration completeness marker and key compatibility record, first-call `session.start`, explicit `runtime.remove-orphans` dry-run/removal, tidy machine state, MCP `tools/list`, active-path isolation and unchanged declared host state. Local Git sources may use an exact commit SHA; remote repositories still require a ref advertised by the remote.

Open an interactive shell (the timeout is owned by the user):

```sh
tools/brain-lab/brain-lab run shell --request-json \
  '{"id":"run-…","shell":"/bin/bash"}'
```

Copy arbitrary external test inputs and outputs:

```sh
tools/brain-lab/brain-lab --json run copy-in --request-json \
  '{"id":"run-…","source":"./test-bundle","destination":"/home/brain/test-bundle"}'

tools/brain-lab/brain-lab --json run copy-out --request-json \
  '{"id":"run-…","source":"/home/brain/results","destination":"./local-results"}'
```

`run recreate` stops and retains the current container until its replacement has started, passed label verification, and become the recorded next generation; a failed replacement restores the previous container. `run discard` deletes the container and run receipt. Neither destroys the baseline or immutable inputs.

## Host agent fixtures

Export a retained, running Brain into a new caller-selected directory without
starting an agent or changing the run:

```sh
tools/brain-lab/brain-lab --json fixture create --request-json \
  '{"run_id":"run-…","skills":["shaping"],"output":"/tmp/brain-lab-fixture"}'
```

The generic `shared/` payload contains one stdio bridge and the exact
active-Brain loaders generated for the requested effective skills. The bridge
uses `docker exec -i` against the receipt-bound container; if that container is
stopped or removed it fails explicitly and never falls back to a host Brain.
`manifest.json` records the run generation, container and image IDs, installed
Core version and normalised hash, equivalent MCP entry sources, and package/loader hashes.
Creation compares bounded run-scope manifests before and after active-Brain inspection;
it publishes nothing unless they are identical. If `--docker` is overridden,
the exported bridge reuses that exact executable name or resolves its selected
path to an absolute executable path. A fixture request accepts at most 32
skills; each exported package is independently bounded by file count and size.
Run-scope manifests stream the current Brain Lab helper through the image-owned
interpreter, so retained runs do not depend on the helper version baked into
their historical image.

Thin client layouts reuse that same payload:

- `clients/codex/home/` is an isolated `CODEX_HOME`, with `config.toml` and a
  relative link to `shared/skills/`. Its selected path is recorded in
  `clients/codex/environment.json`.
- `clients/claude/project/` is an isolated project containing `.mcp.json` and
  `.claude/skills`; its selected project path is recorded in
  `clients/claude/environment.json`. Launch Claude from that directory with the
  recorded `required_arguments`; `--strict-mcp-config` excludes user-scoped MCP
  bindings and `--setting-sources project` excludes user/local settings.

The isolated Codex home contains no authentication and is not a self-running
model profile. For a live Codex drill, retain the normal authenticated host
context and override only `mcp_servers.brain` with the fixture bridge. Do not
copy host credentials into the fixture.

Brain Lab does not launch either client, write real user/project client config,
copy credentials, or modify/stop/recreate the selected run. Creation refuses an
unknown, stopped, wrongly labelled, or incompatible run and refuses any
pre-existing output path rather than merging or overwriting it. Remove the
fixture directory when the host-side investigation is complete; retain or
discard the run separately with the normal run operations. Materialisation
occurs in a private sibling staging
directory and uses an atomic no-replace rename, so concurrent paths are never
merged or overwritten. A failed materialisation or publication reports partial
effects and the retained staging path; Brain Lab does not attempt cleanup, so
the caller can inspect and explicitly remove it.

## Scenarios

Scenarios are optional ordered compositions of the same dispatcher used by direct commands. `${steps.0.resource.id}` references an earlier typed result; scenarios do not have privileged workflow-only operations.

Run the checked-in current-worktree workflow:

```sh
tools/brain-lab/brain-lab --json scenario run --request-json - \
  < tools/brain-lab/scenarios/current-template.json
```

Scenarios stop on a failed or partial/unknown-effect primitive unless `continue_after_failure` is explicitly true. Declared worktrees and vaults receive before/after host-state fingerprints.

## Inspect, export, rebuild, and clean up

```sh
tools/brain-lab/brain-lab --json lab inventory
tools/brain-lab/brain-lab --json cleanup preview --request-json '{"older_than_days":7}'
tools/brain-lab/brain-lab --json results inspect --request-json '{"id":"op-…"}'
tools/brain-lab/brain-lab --json results export --request-json \
  '{"id":"op-…","destination":"./exported-result","redacted":true}'
tools/brain-lab/brain-lab --json baseline verify --request-json '{"id":"baseline-…"}'
tools/brain-lab/brain-lab --json baseline rebuild --request-json '{"id":"baseline-…"}'
tools/brain-lab/brain-lab --json attempt inspect --request-json '{"id":"attempt-…"}'
```

Destroy resources from leaves towards inputs. Every mutation resolves the recorded Docker object and verifies labels first:

```sh
tools/brain-lab/brain-lab --json run discard --request-json '{"id":"run-…"}'
tools/brain-lab/brain-lab --json attempt destroy --request-json '{"id":"attempt-…"}'
tools/brain-lab/brain-lab --json baseline destroy --request-json '{"id":"baseline-…"}'
tools/brain-lab/brain-lab --json seed destroy --request-json '{"id":"seed-…"}'
tools/brain-lab/brain-lab --json source destroy --request-json '{"id":"source-…"}'
tools/brain-lab/brain-lab --json base destroy --request-json '{"id":"base-…"}'
```

For an imported seed or failed preparation attempt, add `"confirm_imported":true`. `cleanup preview` is deliberately non-mutating and excludes imported resources from automatic candidates. It also reports labelled Docker resources whose receipt was lost. Destroy such an orphan only with the exact previewed request:

```sh
tools/brain-lab/brain-lab --json orphan destroy --request-json \
  '{"docker_kind":"container","docker_id":"…","confirm_imported":true}'
```

Recorded resources still use their matching resource destroy/discard operation; orphan destruction refuses them. There is no broad prune command.

## Verification

Fast pure and fake-Docker tests:

```sh
make test-brain-lab
```

Opt-in real Docker current-worktree acceptance:

```sh
make test-brain-lab-docker
```

Run only the Docker configuration boundary acceptance:

```sh
make test-brain-lab-docker-configuration
```

That target places fail-fast native credential helpers on `PATH`, poisons
inherited Buildx/BuildKit selection, and proves public pull/build still use the
pinned local daemon without invoking a helper. To include the authenticated
private-registry case, also set `BRAIN_LAB_REGISTRY_AUTH_CONFIG` to an inline-auth
configuration, `BRAIN_LAB_AUTHENTICATED_IMAGE` to an image it can read, and
optionally `BRAIN_LAB_DOCKER_PLATFORM`. The authenticated case fails rather than
falling back if either configured operation cannot use those credentials; its
build forces remote base-image resolution rather than accepting a cached image.

Run either constituent scenario while iterating:

```sh
make test-brain-lab-current-docker
make test-brain-lab-upgrade-docker
```

To exercise the optional live host-fixture bridge check against an explicitly
retained run without starting a model:

```sh
BRAIN_LAB_HOST_FIXTURE_RUN_ID=run-… \
BRAIN_LAB_HOST_FIXTURE_STATE_DIR=/path/to/brain-lab-state \
  .venv/bin/pytest -q \
  tests/repo/brain_lab/test_host_fixture.py::test_live_fixture_bridge_lists_active_brain_tools
```

The complete design traceability table is `acceptance-matrix.json`. Its owners distinguish checked-in Docker automation from unit coverage and manual live drills. Rows under `test-brain-lab-docker` are exercised by its checked-in current-template and historical-upgrade scenarios; `test-brain-lab-docker-configuration` owns the native configuration boundary. Slow Docker workflows are not part of routine pre-commit tests.

## Troubleshooting

- **Docker unavailable** — run `docker version` and verify both client and server are present.
- **Unsupported Brain version** — capture a version covered by `compatibility.json`; the lab never substitutes current-worktree commands for an unknown release.
- **Core mismatch** — VERSION alone is insufficient. Provide the exact source whose normalised Brain Core fingerprint matches the imported vault.
- **Failed preparation** — inspect the returned attempt ID/container directly and inspect its operation result; failed attempts are never promoted.
- **Partial evidence** — a stream may have reached its retention limit, authenticated output may have been deliberately discarded, or interactive output may not have been captured. `commands.json` distinguishes truncation from authenticated redaction; `shell.json` records uncaptured interactive output. Raise `--stream-limit` only for truncation.
- **macOS “access data from other apps” prompt** — Docker Desktop socket access can trigger this prompt for the host app, and some app/process identities may be prompted repeatedly. Use a trusted terminal or host app whose Docker access is approved; Full Disk Access is not a brain-lab prerequisite in itself.
- **Disk growth** — capture/import records a conservative host-space preflight. Inspect `lab inventory` and `cleanup preview`, review references, sizes and labelled orphans, then destroy exact resources. There is intentionally no broad prune command.
