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

## Exit categories

| Exit | Meaning |
|---:|---|
| 0 | `ok` |
| 1 | known `partial` |
| 2 | usage, invalid request or domain conflict |
| 3 | authority, dependency or provider unavailable |
| 4 | infrastructure failure or unknown outcome |

Human output and JSON output are projections of the same result. Diagnostics never replace the structural envelope in JSON mode.

## Typed Python

Python consumers construct the sealed request type for a known command and invoke it through `CommandApplication(context).invoke(request)`. Dynamic infrastructure consumers resolve through the catalogue-bound `ApplicationAdapter`; free command strings are permitted only at that explicit adapter boundary.

The trusted `InvocationContext` contains selected-Brain identity, authenticated authority, dependency tier, one capability snapshot, providers, invocation/correlation identity, receipt writer and effect facilities. Executors do not rediscover those facts from environment variables.

## Internal script modules

Files such as `create.py`, `edit.py`, `read.py`, `repair.py`, `session.py`, `upgrade.py` and domain packages remain implementation providers where application or launcher owners use them. Their old independent aggregate parsers and compatibility entry points are not the public command grammar. `start_shaping.py` is removed; use `shaping.start` through `command.py`.

Machine-global operations do not run through selected-Brain `command.py`. The versioned CLI distribution owns the separate stdlib-safe launcher catalogue and its install, upgrade, registry, runtime, MCP and diagnostic owners.

## Dependency boundaries

Application foundations remain tier-strict:

- `bootstrap` uses the standard library and bootstrap-safe Brain modules;
- `portable` adds ordinary local Brain functionality without the managed runtime;
- `managed` may use the selected Brain's managed dependencies.

These are ordered dependency tiers only. Locality, authority and providers remain orthogonal. Lower-tier packages do not import adapter, MCP SDK, terminal renderer or higher-tier implementation packages.
