# Direct Script and Python Command Interfaces

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

Files such as `create.py`, `edit.py`, `read.py`, `repair.py`, `session.py`, `upgrade.py` and domain packages remain implementation providers where application or launcher owners use them. Their old independent aggregate parsers and compatibility entry points are not the public command grammar. `permission_admin.py` is a CLI-owned internal subprocess boundary that receives its operator secret only through trusted process context; it is not a direct-script command. `start_shaping.py` is removed; use `shaping.start` through `command.py`.

Machine-global operations do not run through selected-Brain `command.py`. The versioned CLI distribution owns the separate stdlib-safe launcher catalogue and its install, upgrade, registry, runtime, MCP and diagnostic owners.

## Dependency boundaries

Application foundations remain tier-strict:

- `bootstrap` uses the standard library and bootstrap-safe Brain modules;
- `portable` adds ordinary local Brain functionality without the managed runtime;
- `managed` may use the selected Brain's managed dependencies.

These are ordered dependency tiers only. Locality, authority and providers remain orthogonal. Lower-tier packages do not import adapter, MCP SDK, terminal renderer or higher-tier implementation packages.

Portable `vault.check` inspects semantic metadata locally and verifies model loading in the selected managed interpreter. Missing dependencies produce findings; timeout or malformed managed output produces a bounded inspection finding. Warm-up also isolates semantic work in that interpreter, and explicit `runtime.warmup` retries a previously deferred component.

## Grok in setup and maintenance

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
