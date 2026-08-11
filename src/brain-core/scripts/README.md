# Brain Core command application

`scripts/` contains the selected-Brain application and its lower-level implementation packages. It is not a collection of independent public command-line programs.

## Public selected-Brain entry point

The supported direct projection is:

```bash
python3 .brain-core/scripts/command.py <noun> <verb> \
  --request-json '<object>' [--vault PATH] [--workspace PATH] \
  [--operator-key KEY] [--dry-run] [--json]
```

Examples:

```bash
python3 .brain-core/scripts/command.py command list --request-json '{}' --json
python3 .brain-core/scripts/command.py command describe \
  --request-json '{"target_command_id":"artefact.create"}' --json
python3 .brain-core/scripts/command.py artefact read \
  --request-json '{"reference":"design/brain"}' --json
```

Use command discovery for the exact installed schema and minimal request. `command.py` accepts only the canonical noun/verb identity and strict JSON object. Selection, operator identity and dry-run are trusted adapter context; they cannot be smuggled into semantic fields.

The direct projection emits the same `brain.command-result/1` envelope and exit categories as MCP and CLI:

- 0 — `ok`;
- 1 — `partial` with known committed effects;
- 2 — invalid request or domain failure;
- 3 — authority or capability unavailable;
- 4 — infrastructure failure or unknown mutation outcome.

## Application structure

```text
scripts/
├── command.py                 canonical direct projection
├── _application/              typed semantic application
├── _command_interface/        trusted local composition and receipts
├── _bootstrap/                stdlib-safe bootstrap owners
├── _portable/                 portable implementation seams
├── _common/, _lifecycle/, …   lower-level domain support
└── upgrade.py                 pre-cutover/source recovery launcher
```

`_application/` is a real internal application package. It owns:

- sealed request and result payload types;
- `CommandApplication(context).invoke(request)`;
- the selected-Brain catalogue and dynamic resolver;
- dependency tier, locality, provider, authority, effect and retry metadata;
- structural outcome receipts and recovery references;
- mechanical MCP, CLI, script and Python projection metadata.

It does not import `argparse`, the MCP SDK, terminal renderers, environment selectors or concrete provisioning. Lower-level packages never import back into `_application`.

`_command_interface/` is the trusted composition boundary for direct selected-Brain invocation. It resolves the selected vault/workspace, authenticates profile authority, composes providers, chooses the current tier and persists privacy-minimal outcome receipts. It does not own command semantics.

## Dependency planes

Commands declare one ordered minimum tier:

- **bootstrap** — stdlib-safe discovery and recovery base;
- **portable** — portable local Brain operation;
- **managed** — managed-runtime operation.

Locality and providers are orthogonal. A selected-Brain command never becomes machine-global because it is bootstrap-tier; an optional provider never raises the minimum tier of an otherwise complete command. Unavailable commands return structural capability details and one next action instead of provisioning or handing off implicitly.

## Other projections

- `brain_mcp/` registers every MCP-eligible catalogue entry under its canonical `<noun>.<verb>` identifier.
- CLI 2 resolves application discovery from the selected Brain and invokes that Brain's `command.py` process.
- Typed Python callers construct a sealed request and invoke `CommandApplication` with trusted context.

These are adapters over one application owner, not separate implementations.

## Legacy modules

Many lower-level modules retain executable guards for repository tests, maintenance internals or platform recovery. They are not a supported semantic grammar. Do not add a new public operation by creating another top-level script or Python wrapper.

To add or change an operation:

1. define or revise one sealed request/result pair under `_application/`;
2. implement one application executor;
3. add one catalogue entry with explicit tier, locality, providers, authority, effects, retry and projection eligibility;
4. register the request in the dynamic resolver;
5. prove catalogue, projection, adapter and cross-surface parity;
6. update versioning and discovery evidence according to the command/interface policy.

If alternatives differ in required fields, result/error contract, authority, tier, locality, atomicity, retry or effects, model separate commands instead of adding a discriminator bucket.

## Versioning

- Breaking MCP identity/request projection changes increment the interface epoch.
- Breaking semantic input, result, stable-code or meaning changes increment the command version.
- Result, catalogue, launcher and proxy protocols version independently.
- Catalogue fingerprints validate an exact static set; they are not compatibility versions.

Local MCP and CLI calls bind to installed versions. Callers do not supply command versions.

## Testing

Use the immutable installed command-vault seed and isolated clones for filesystem/effect tests. Pure request, result, catalogue, parser and projection tests should not clone a vault. Every effect-bearing test must distinguish no effects, known partial effects and unknown outcomes; absence of a receipt never proves no effect.

The release gates reconstruct generated facts from the authoritative catalogue, validate every eligible projection, exercise real supported-client MCP declarations, rehearse fresh install and checked upgrade, and run the full serial repository suite.

See [`docs/functional/scripts.md`](../../../docs/functional/scripts.md), [`docs/functional/mcp-tools.md`](../../../docs/functional/mcp-tools.md), and [`docs/functional/cli.md`](../../../docs/functional/cli.md).
