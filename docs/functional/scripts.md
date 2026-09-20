# Direct Script and Python Command Interfaces

`configure.py approvals` delegates to the [managed approvals](approvals.md) machine
owner. Native install accepts separate `--approval-client`, `--approval-scope` and
`--approvals` selections. With existing opt-ins, direct install, upgrade and
registry mutations use the same compatible machine owners; a missing writer is an
error before mutation, not a fallback to legacy writes.

## Managed dependency lifecycle

The shared stdlib runtime owner consumes `.brain-core/brain_mcp/requirements.txt`
and the complete optional `requirements-semantic.txt`. Both exports determine
runtime identity and upgrade dependency-change detection. Installation uses
`pip --no-deps -r`; fresh install, dependency-syncing upgrade, semantic provision
and explicit `runtime.repair` verify all applicable package versions plus
`pip check` before recording versioned readiness. Healthy handoff uses matching
readiness without a package audit. Dependency-sync opt-outs remain explicit;
copying core files does not prove dependency convergence. Contributor generation
and certification commands are documented in the
[dependency workflow](../contributor/dependencies.md).

The selected Brain owns one public direct command projection:

```text
.brain-core/scripts/command.py <noun> <verb> \
  --request-json JSON|- \
  [--vault PATH] [--workspace PATH] [--operator-key KEY] \
  [--dry-run] [--json]
```

It resolves the exact command from the installed application catalogue, decodes the same sealed request used by MCP and CLI, composes trusted selected-Brain context, invokes the canonical application owner and projects `brain.command-result/1`.

Examples:

```bash
python3 .brain-core/scripts/command.py artefact read \
  --request-json '{"reference":"Designs/Example.md"}' --json

printf '%s' '{"query":"command architecture"}' | \
  python3 .brain-core/scripts/command.py artefact search \
    --request-json - --json

python3 .brain-core/scripts/command.py command describe \
  --request-json '{"target_command_id":"artefact.create"}' --json
```

## Contract parity

MCP, CLI, direct script and typed Python share:

- the command ID and command version owned by the request type;
- strict required, optional, nullable and enum semantics;
- authority, tier, locality, provider, effect and retry classification;
- the same executor and structural result;
- stable error codes and exit categories;
- durable receipt handling for effect-bearing outcomes.

Adapter-only concerns remain outside the semantic request. `--vault`, `--workspace`, `--operator-key`, `--dry-run`, process rendering and invocation identity are trusted composition inputs, not command fields.

For artefact creation, `shaping.start` v2, the existing semantic lifecycle owners and the four
document mutation owners, semantic
`workspace_context` in request JSON accepts `workspace/{key}` or `global`, never
a filesystem path. Document targets other than artefacts reject this selector.
The typed Python construction value is `brain_application.values.WorkspaceSelector`;
CLI, script, Python and MCP share its strict schema and resolver. See the
[workspace mutation contract](cli.md#effective-workspace-context-for-content-mutations)
for parent/tag precedence and preparation freshness. `artefact.set-workspace`
explicitly reassigns the complete owned subtree using that context as its
destination; `global` clears membership only for this dedicated transition.
Shaping creates transcripts with selected membership, default parent and tags;
continuation preserves existing membership/parent and restores configured tags
on the source and transcript. The linked contract above covers its composite
preparation and partial-result behaviour.

The direct projection honours the same credential permissions and configured initial authorisation as MCP. A managed CLI job provides one private lifetime for `access.prepare`, `access.request` and `access.reduce`; standalone calls retain initial authorisation and their own principal-scoped receipts. Permission administration belongs to the CLI-only `permission.set-profile` launcher and is absent from this script and the typed application catalogue.

Frontmatter transport fields are JSON objects; typed Python constructors retain immutable field tuples. Artefact selectors resolve short or qualified singular/plural names through the configured taxonomy and return its canonical frontmatter type. MCP projects the dotted command ID to `noun_verb`; direct scripts keep noun/verb arguments.

## Exit categories

| Exit | Meaning |
|---:|---|
| 0 | `ok` |
| 1 | known `partial` |
| 2 | usage, invalid request or domain conflict |
| 3 | authority, dependency or provider unavailable |
| 4 | infrastructure failure or unknown outcome |

Human output and JSON output are projections of the same result. Diagnostics never replace the structural envelope in JSON mode. Unexpected failures produce bounded public stderr without a traceback or raw exception detail. Once trusted context exists, its diagnostic sink receives the full failure with command and correlation metadata.

## Typed Python

Python consumers import the supported kernel from `brain_application`, construct sealed request types from `brain_application.requests`, and obtain their construction values from `brain_application.values` or a narrow domain module such as `brain_application.documents`. Trusted context contracts, including dependency and availability enums and receipt ports, are exported by `brain_application.context`. Invoke requests through `CommandApplication(context).invoke(request)`. Importing the kernel does not load command owners; importing the all-requests or all-values modules is an explicit opt-in to every dependency tier. The internal `_application` tree owns execution and registration and is not a supported integration surface. Dynamic infrastructure consumers resolve through the catalogue-bound `ApplicationAdapter`; free command strings are permitted only at that explicit adapter boundary.

The trusted `InvocationContext` contains selected-Brain identity, current permission and authorisation ports, dependency tier, capability snapshot, providers, invocation/correlation identity and owned outcome receipts. Executors do not rediscover these facts from environment variables. Ordinary owners must admit the operation under their existing domain guard before returning success; every entered observation or mutation has an immutable intent and independent execution/effect outcome.

Local Python integrations can use `brain_application.local.LocalContextComposer(vault_root=...)`, invoke `CommandApplication(composer.compose(command_id=..., invocation_id=...)).invoke(request)`, then close the composer. This explicit infrastructure import resolves the same current credential permissions and initial policy as CLI and MCP. A standalone Python context cannot manufacture exceptional consent; a trusted adapter must attach an actual private instance owner. Advanced adapters can implement the public authorisation and owned-receipt ports directly. Discovery uses a batched, non-consuming observation from that authorisation service; it never grants permission to execute.

## Internal script modules

`compile_router.py` and `define.py` share taxonomy parsing. Filename patterns in
simple and advanced `## Naming` contracts reject absolute paths, POSIX/Windows
separators, drive prefixes and dot traversal segments before definition writes
or router publication. Rendering validates cached patterns too. `create.py`,
shaping, and backlink writers use the purpose-specific `safe_write_artefact()`
boundary, while document/lifecycle and repair writers select the active or
archive-only capability through `safe_write_active_or_archived_artefact()`; both check the
fully resolved destination. `rename.py` uses the same destination policy for
moves and verifies that each resolved source remains in its requested scope.
Ordinary artefact destinations
cannot enter `_Config/` or dot-prefixed system folders. Archived artefact,
configuration, and internal writers retain narrower separate capabilities.

Files such as `create.py`, `edit.py`, `read.py`, `repair.py`, `session.py`, `upgrade.py` and domain packages remain implementation providers where application or launcher owners use them. Their old independent aggregate parsers and compatibility entry points are not the public command grammar. `permission_admin.py` is a CLI-owned internal subprocess boundary that receives its operator secret only through trusted process context; it is not a direct-script command. `start_shaping.py` is removed; use `shaping.start` through `command.py`.

For repository maintenance and last-resort recovery, `compile_router.py` accepts
an explicit `--vault`; its `--help` path performs no vault discovery or
compilation. The retained `create.py` compatibility entry point republishes the
compiled router after a committed create, so consecutive recovery creates do
not require a manual compile between commands. If that post-commit refresh
cannot complete, creation still reports its committed result with an explicit
`runtime.refresh-router` warning rather than inviting a duplicate retry.
This compatibility route is restricted to wholly unscoped recovery vaults and
cannot create workspace hubs. If any workspace hub or membership metadata exists,
it fails closed and directs callers to `artefact.create` through `brain` or
`command.py`. Legacy `edit.py`, `rename.py`, and `lifecycle.py` mains are internal
test/maintenance seams, not supported semantic routes; do not use them to bypass
workspace preparation, policy, lifecycle guards, or index completion.

Machine-global operations do not run through selected-Brain `command.py`. The versioned CLI distribution owns the separate stdlib-safe launcher catalogue and its install, upgrade, registry, runtime, MCP and diagnostic owners.

The legacy `setup.py workspace` entry point retains its historical local
binding and ignore-scaffold behavior for compatibility; it does not ensure a
canonical workspace hub. Use `command.py workspace setup --workspace PATH`
or `brain workspace setup --workspace PATH` for the compound registration and
binding contract. This operation is unavailable over MCP because its second
boundary needs caller filesystem access.

## Dependency boundaries

Application foundations remain tier-strict:

- `bootstrap` uses the standard library and bootstrap-safe Brain modules;
- `portable` adds ordinary local Brain functionality without the managed runtime;
- `managed` may use the selected Brain's managed dependencies.

These are ordered dependency tiers only. Locality, authority and providers remain orthogonal. Lower-tier packages do not import adapter, MCP SDK, terminal renderer or higher-tier implementation packages.

Portable `vault.check` inspects semantic metadata locally and verifies model loading in the selected managed interpreter. Missing dependencies produce findings; timeout or malformed managed output produces a bounded inspection finding. Warm-up also isolates semantic work in that interpreter, and explicit `runtime.warmup` retries a previously deferred component.

## Grok in setup and maintenance

New MCP installation/configuration requires an explicit client. Use
`install.sh --non-interactive --client all /path/to/brain` (Windows: `-Client all`),
or choose one client. Scaffold-only installation needs no client selection.
`configure.py mcp --client ... --user` delegates to the compatible installed
machine CLI without resolving a Brain; project/local operations share the
canonical registration planner. `repair.py mcp` remains vault-local and repairs
only recorded vault-self project projections. Cross-root and user repair belongs
to the [launcher breadth selectors](cli.md#mcp-registration-and-repair).

Upgrade runs machine ownership migration and Brain-breadth reconciliation after
the coordinated CLI/Core/runtime cutover. The shell uninstaller delegates to
the canonical launcher uninstall owner and stops on incomplete cleanup. Legacy
records require explicit `brain mcp migrate`, not adoption by normal repair.

The supported `install.py --client`, `configure.py mcp --client`,
`configure.py agent-skills --client` and interactive `setup.py` selections
include `grok`; `all` includes all three clients. Grok supports user and project
scope, including vault-self registration. The native CLI commands are shown in
[native Grok setup](cli.md#native-grok-setup). `repair.py mcp` and upgrade
reconciliation inspect existing Grok project registrations and their owned
startup rules. Standalone workspace bootstrap also accepts `--surface grok`.

Bootstrap and document reads use the same bounded application results in every
projection. Finish `session.start` pages until `bootstrap_complete` is true.
For document reads, repeat the same selectors with `cursor: range.next_cursor`
until null; revisions prevent mixing source versions between pages.

### Router repair contract

`runtime.refresh-router` verifies the same source fingerprints as mutation
admission and verifies the persisted result. An unchanged, valid router is a
no-op; `force: true` unconditionally rebuilds using the same algorithm. A source
change detected after rebuilding is a partial repair, with an explicit command
next action through CLI/MCP. `vault.check(check="router")` uses authoritative
content validation and identifies an affected source path when available.

`runtime.status` reports recorded warm-up progress, labelled `recorded-warmup`;
ready is not a current-cache health assertion. Use its `router_check` action for
current router diagnosis. Artefact creation and lifecycle-field commands maintain router and lexical
state before returning success; semantic encoding remains separate.
