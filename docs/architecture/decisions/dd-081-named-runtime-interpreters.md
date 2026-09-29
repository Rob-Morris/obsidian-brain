# DD-081: Named runtime interpreters through one launch owner

**Status:** Accepted
**Extends:** DD-048, DD-077

## Context

Every Brain process on the managed runtime (DD-048) executes the venv's
`bin/python`, which resolves to the base interpreter, so Activity Monitor,
`top` and other kernel-name views show `python3.12` or `Python` for the MCP
proxy, the MCP server, warm-up workers and managed-tier CLI jobs alike. Telling
a Brain process from any other Python takes forensic work with `ps` argv.

Making the name a launch input (a persisted role path, or `setproctitle`) would
turn presentation into a correctness dependency: identity checks, client
configs, sentinels and older Cores all compare `sys.executable` against the
canonical `bin/python`, and the dependency contract (DD-077) keys every venv on
its exact closure.

## Decision

A conformed managed venv publishes two role-named links to its base
interpreter, `bin/brain-mcp-python` and `bin/brain-cli-python`: hard links on
macOS, symlinks on Linux, unsupported elsewhere. `conform_runtime` publishes
them right after it writes the readiness sentinel, so every lifecycle command
(install, upgrade, runtime and MCP repair, semantic enable and repair) reaches
the step and a plain launch or dry run never does. Each candidate is staged
under a per-process name, probed once with `-I` for canonical
`sys.executable`, canonical `sys.prefix` and the expected kernel name, then
published with an atomic replace. A failed probe (framework builds,
relative-rpath builds) publishes nothing.

`_common/_venv.py` owns the launch of a managed interpreter. `managed_command`
decides once: the kernel executes the role file only when `argv[0]` is the
absolute canonical interpreter of a managed venv, a role is known and the
role's file is usable (`os.path.samefile` with `bin/python`); argv is never
changed. `ManagedCommand.run`, `.popen` and `.exec` pass argv and environment
together, adding the role file as the executable only when one is chosen, so an
unnamed launch is the plain `subprocess` call; `run_managed` serves injected
runners. Every launch of
a managed interpreter in `src/brain-core` and `cli/` goes through this owner;
`tests/test_managed_launch_contract.py` enforces it over AST references with a
function-keyed, reasoned allowlist.

`BRAIN_RUNTIME_ROLE` carries the role to children. `brain mcp serve`, the proxy
(its self re-exec, server child and handoff) and the CLI command tier set it;
everything else inherits it. The proxy names itself in an early `__main__`
guard so project-scope client configs stay canonical.

## Alternatives Considered

- Set `argv[0]` to the role path: needs path normalisation across about a dozen
  identity checks and carries handoff and older-Core risks.
- `setproctitle`: a compiled dependency that re-keys every venv, costs about
  26 ms per macOS process, and does not change the kernel-name column.
- Persist the role path in client configs: makes the name a launch dependency.
- Copy the interpreter under the role name: copied binaries drift and get scanned.

## Consequences

- A person can tell at a glance whether a process is Brain MCP or a Brain CLI
  job on macOS non-framework builds and on Linux (`brain-mcp-pytho`, 15 chars).
- macOS framework builds (Homebrew, python.org) and Windows keep today's names;
  argv still identifies Brain in `ps` and htop.
- Naming is presentation only: a missing, stale or unsupported role file means
  the launch runs `bin/python` exactly as before. Nothing in Core reads kernel
  names, and role files live only inside `~/.brain/venvs`, outside every vault.
- Portable-tier `brain` commands run on the launcher's Python and stay unnamed.

## Verification

`tests/test_role_interpreters.py` covers the owner's decision matrix,
materialisation, staleness, entry roles and the `brain mcp serve` exec;
`tests/test_managed_launch_contract.py` enforces the launch policy;
`tests/test_role_interpreters_observed.py` starts the proxy, its server, a
warm-up worker, a handoff and a managed-tier CLI job on a temporary venv and
reads their kernel names (`/proc/<pid>/comm` on Linux, `ps -o ucomm` on macOS).
