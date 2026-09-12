# Brain CLI 3

The `brain` CLI is the machine-local projection of the Brain command architecture. CLI 3 keeps canonical command IDs in predictable noun/verb form while giving launcher-owned machine operations concise, domain-explicit entry points. It removes machine-implementation names and duplicate commands rather than carrying compatibility aliases.

## Command grammar

```text
brain [selection] <launcher-entry-point> [--request-json JSON|-] [--json] [--dry-run]
brain [selection] <noun> <verb> [--request-json JSON|-] [--json] [--dry-run]
brain command list [discovery filters] [--json]
brain command describe <command-id> [--owner application|launcher|all] [--json]
brain --version
brain --help
```

Command IDs use `<noun>.<verb>`. Selected-Brain application commands use the same noun and verb as two CLI words. Launcher commands own an explicit entry point: the redundant `brain` noun is collapsed (`brain.doctor` → `brain doctor`), while other nouns remain visible (`runtime.inspect` → `brain runtime inspect`). For example:

```bash
brain artefact read --request-json '{"reference":"Designs/Example.md"}' --json
brain artefact create --request-json '{"type":"living/wiki","title":"Example","content":{"source":"inline","content":"Body"}}' --json
brain doctor --request-json '{}' --json
brain runtime inspect --vault /path/to/brain --request-json '{}' --json
```

There are no compatibility aliases, aggregate action buckets, parser spelling aliases or legacy-mode translations. Request fields are passed in one JSON object so MCP, CLI, direct script and typed Python use the same semantic request where those projections are eligible.

The CLI 3 launcher break is deliberate:

| Removed command | Replacement | Purpose |
|---|---|---|
| `brain machine migrate-legacy` | `brain migrate-legacy-installations` | Migrate legacy Brain installations on this machine. |
| `brain machine prune-runtimes` | `brain runtime remove-orphans` | Remove orphaned managed runtimes. |
| `brain backfill` | `brain register` | Register an installed Brain; the removed command duplicated this operation. |
| `brain prune` | `brain registry remove-stale` | Remove stale local Brain registry entries. |
| `brain runtime resolve` and `brain runtime resolve-runnable` | `brain runtime inspect` | Report the expected managed runtime and the selected runnable Python source together. |

`brain resolve` remains the direct registry lookup from Brain ID to vault path. These launcher commands are CLI-only; the MCP catalogue is unchanged.

`--request-json -` reads the object from standard input. Unknown fields, malformed JSON, unknown launcher entry points and application commands with anything other than one noun and one verb fail as request errors.

## Discover commands instead of memorising them

The installed catalogues are authoritative. Use discovery for the exact command set, request schema, version, owner, safety class, dependency tier, authority and current availability:

```bash
brain command list --owner all --page-size 100 --json
brain command list --owner application --query artefact --json
brain command describe artefact.create --owner application --json
brain command describe brain.upgrade --owner launcher --json
```

`application` commands are owned by the selected Brain. `launcher` commands are machine-global and owned by the installed CLI distribution. The composed view preserves that owner and each catalogue's fingerprint; it does not create a third semantic catalogue.

## Select a Brain

Selection options are global and mutually constrained:

- `--vault PATH` selects an installed local Brain explicitly.
- `--brain ID` resolves one registered local Brain.
- `--workspace PATH` supplies the caller-local workspace used by normal workspace binding resolution.
- With no explicit selector, the CLI uses the canonical local resolution ladder.
- `--operator-key KEY` authenticates the application command against the selected Brain's profiles.

Launcher commands may run without a selected Brain when their schema permits it. Application commands always execute through the selected Brain's own `.brain-core/scripts/command.py`; the machine-global CLI does not import or emulate another Brain's application semantics.

### Skill sources and exposure

Selected-Brain commands `skill.list`, `skill.status`, `skill.add-git`,
`skill.update` and `skill.detach` own package source state. Launcher commands
`skill.expose` and `skill.unexpose` own explicit writes to Claude and Codex
discovery directories. For example:

```bash
brain skill update --request-json '{"name":"shaping"}' --json
brain skill expose --request-json \
  '{"name":"shaping","client":"all","scope":"global"}' --json
```

The exposure launcher still requires an active Brain so it can validate the
effective skill. With no explicit selector, normal resolution includes the
machine's default Brain. Project exposure resolves an existing canonical binding
for `--workspace` first and otherwise uses the machine default; it never creates
or changes a binding.

### External access approval

When `vault.access.elevation_policy` is `external`, an agent's `access.request` returns a pending `request_id` without activating the command. A human or separately trusted local operator approves it through the CLI-only launcher owner:

```bash
brain --vault /path/to/brain access approve \
  --request-json '{"request_id":"access-request-…"}' --json
```

If `--operator-key` is omitted, an interactive terminal prompts without placing the secret in the request or receipt. Non-interactive use must supply `--operator-key`. The key must identify a different registered operator whose profile ceiling covers every requested command; a principal cannot approve its own request. `access.approve` is intentionally absent from MCP and selected-Brain `command.py`; agents receive only `access.status`, `access.request` and `access.reduce`.

## Dependency planes

The command catalogue declares `bootstrap`, `portable` or `managed` as an ordered minimum dependency tier. The CLI runs bootstrap and portable application commands with its Python 3.12+ launcher, and resolves the selected Brain's managed runtime only for managed commands. Locality and provider requirements remain separate catalogue facts; a higher dependency tier does not imply machine-global ownership or remote transport.

## Results and exit categories

`--json` emits exactly one `brain.command-result/1` envelope. Human mode renders the same envelope without changing its semantics.

- `ok` contains a typed result.
- `partial` lists each known committed effect. When machine-local cleanup is
  still required, typed `error.details.recovery_paths` lists the exact absolute
  paths that remain.
- `error` has `effects: none` or `effects: unknown`.
- Unknown mutation outcomes are non-retryable and include an outcome reference for `invocation.read`.

Exit categories are stable across CLI and direct script:

| Exit | Category |
|---:|---|
| 0 | Success |
| 1 | Known partial outcome |
| 2 | Usage, request or domain error |
| 3 | Authority or capability unavailable |
| 4 | Infrastructure failure or unknown mutation outcome |

## Launcher recovery and old Brains

CLI 3 can identify and recover an installed Brain older than 0.55.0, but it does not translate old grammars. Launcher-owned version, doctor, install and upgrade/recovery commands remain available. Attempting an application command returns structural `upgrade_required`; that Brain's own legacy scripts remain directly invocable until the Brain is upgraded.

`brain.upgrade` v2 performs a complete-registry preflight and coordinates Brain Core 0.55+, the installed CLI, catalogue, manifest and proxy contracts. Known other pre-cutover Brains require `acknowledge_global_cli_cutover: true`. Stale registry IDs require an exact sorted `excluded_stale_brain_ids` list; unknown registry scope cannot be waived.

After provisioning the target managed runtime, upgrade reconciles any existing current-vault Claude and Codex MCP registrations through the canonical `repair.py mcp` owner; vaults without project registrations remain untouched. Registration or readiness failure is a known partial outcome with explicit recovery guidance, not a false success. Upgrade then starts or joins the selected Brain's canonical runtime warm-up and waits for a recorded `ready` state. It also performs a read-only machine-topology inspection; when unused shared runtimes are proven orphan candidates, it reports `brain runtime remove-orphans --dry-run` and the explicit removal command without deleting machine-global state itself.

## Installation

The installer writes a versioned distribution under the selected prefix and a small platform bootloader under `bin/`:

- Unix-like user install: `~/.local/bin/brain` and `~/.local/lib/brain-cli/3.1.8/`.
- Native Windows user install: `%LOCALAPPDATA%\Programs\Brain\bin\brain.cmd` and the adjacent `lib\brain-cli\3.1.8\` distribution.

The distribution contains the launcher application plus the Brain Core payload needed for install, upgrade and selected-Brain execution. Installation and replacement verify a content manifest and executable identity; failed replacement restores the proven old binary/distribution pair or retains recovery material and reports the outcome as unverified. Failed upgrade results carry every known absolute recovery path in the structural error and durable launcher receipt: residual staging material after a verified rollback is a known partial outcome, while unverified rollback remains outcome-unknown. Standalone human output lists the same paths before the failure message. Once the new pair is verified, failure or interruption while removing an old backup is committed post-upgrade recovery work and never rolls Brain Core back to an older version. Both the launcher result and standalone distribution JSON list the surviving `cleanup_recovery_paths`.

The bootloader requires Python 3.12 or newer. `BRAIN_CLI_VERSION` is `3.1.8`; `BRAIN_INSTALL_REF` is `v0.63.0`.
