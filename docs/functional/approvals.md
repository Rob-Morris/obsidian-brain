# Managed client approvals

Brain can manage an explicitly selected subset of Codex or Claude Code approval
policy. This is separate from MCP transport installation, Brain credentials and
exceptional-operation consent. Opus is a Claude model, not another client adapter.

## Opt in

Upgrades from before 0.70.3 to 0.70.3 or later include an optional setup
follow-up, also previewed by upgrade dry-run. It points to `brain approvals
inspect --json`, which inspects all supported clients and both surfaces in user
scope without changing policy. The notice does not opt in, adopt manual rules,
remove legacy rules, or block unattended upgrades. Later upgrades and same-version
re-applies do not repeat it. Choose the configuration selections explicitly below.

Preview before writing:

```bash
brain approvals inspect --request-json '{"client":"all","scope":"user","surfaces":["mcp"]}' --json
brain approvals configure --request-json '{"client":"all","scope":"user","surfaces":["mcp"]}' --json
# Separately opt into Claude CLI approvals on supported POSIX hosts:
brain approvals configure --request-json '{"client":"claude","scope":"user","surfaces":["cli"]}' --json
```

`client`, `scope` and `surfaces` are required for configuration. Choose `codex`,
`claude` or `all` supported approval clients; `user`, `project` or Claude-only
`local`; and `mcp`, `cli` or both. Project/local commands use the explicit
`--workspace` directory or the caller directory. Grok approvals are not supported.
Inspection and `--dry-run` never write policy. Successful no-effect launcher
dry-runs also need no durable outcome receipt storage; actual or uncertain effects
still retain their recovery evidence. A per-target `blocked` result does
not prevent independent supported selections from completing. Check
`result.complete` and every target's state, not just the command envelope:
inspection/configuration can successfully produce an incomplete assessment.

Install can opt in separately from its transport choices:

```bash
bash install.sh --non-interactive --client all \
  --approval-client all --approval-scope user --approvals mcp /path/to/new-brain
```

PowerShell uses `-ApprovalClient`, `-ApprovalScope` and `-Approvals` with the same
values. The typed `brain install` request uses `approval_client`,
`approval_scope` and `approval_surfaces`. Omission creates no opt-in; existing
opt-ins still follow supported registration and contract transitions. Scaffold,
transport and approval outcomes remain distinct. Approval failure does not undo
an already-created vault. The direct `configure.py approvals` adapter delegates
to the same machine CLI; exact item adoption uses the CLI JSON interface.

## Policy and trust

There is one profile, `brain-normal/1`: ordinary observation and content commands
are allowed, including normal writes and repairs. Non-granting controls and proxy
status are included. Exceptional commands, `access.request`, proxy refresh/restart
and privileged launcher operations retain native review. Unknown classes receive
no new allowance; they retain the host's other policy. There is no promise that
an unknown tool always prompts, or that a native reviewer is necessarily human.

The set comes from installed generated `approval-contract.json` files and the
canonical application, launcher and proxy metadata. A registered Brain without a
readable supported contract blocks widening: upgrade/repair its installation or
resolve its stale registration. New/renamed normal commands are included during
reconciliation without another opt-in. This trusts future release classifications,
not just today's names. A new policy trust boundary requires a new opt-in.

User policy covers all known locally registered Brains. Classification disagreement
retains review. CLI policy uses that same known-Brain set even in project scope:
command arguments can select another Brain. Project scope controls where a host
loads a rule; it cannot confine `--vault` to one Brain. Unregistered explicit
paths, manually retargeted servers and executables replaced outside supported
commands are outside this registry-derived guarantee.

CLI rules cover the recorded absolute Brain executable followed by canonical
command words, with arguments after the verb. They do not grant arbitrary shells,
raw Python scripts, a bare `brain` resolved through a changed PATH, or selectors
placed before the command. Shell approval can permit execution outside a host's
sandbox; select MCP-only if that is not the intended trust grant. Brain permission
ceilings and instance-owned exceptional consent are unchanged.

## Native files and activation

| Client | MCP policy | CLI policy |
|---|---|---|
| Codex | `config.toml`, exact `mcp_servers.brain.tools.<tool>.approval_mode` fields | Currently blocked; existing `rules/brain.rules` ownership can be removed/detached |
| Claude Code | Exact `mcp__brain__<tool>` rules in `permissions.allow`/`ask` | Exact absolute-command prefixes in `permissions.allow`/`ask` |

Codex user files live under `CODEX_HOME` or `~/.codex`; Claude user files under
`CLAUDE_CONFIG_DIR` or `~/.claude`. Project files live under the selected native
`.codex`/`.claude` directory; Claude local policy uses `settings.local.json`.
For Claude local scope on POSIX, select the main checkout root explicitly;
nested directories and linked worktrees are refused rather than silently writing
policy with a broader native scope. Project scope remains available there.
Root overrides must be absolute. A changed root/executable is diagnosed rather
than followed as authority to edit a new destination. Native Windows shell
projection is not certified and returns blocked; MCP remains separately selectable.
Minimum tested versions are Codex 0.155.1 and Claude Code 2.1.278.
Codex CLI approval management is currently unsupported for **all executable
paths**. Native probes with Codex 0.155.1 found that quoting even a space-free
executable could bypass prompt matching, despite passing the standalone rule
parser. A space-free path or a symlink is not a reliable workaround: Brain cannot
control how a host spells a shell invocation. Inspect/configure/adopt/repair
report this surface as `blocked`; `all` plus `cli` therefore reports incomplete
coverage while independent supported surfaces can complete. Newer clients do not
automatically lift this restriction without native recertification.

Existing Codex CLI files are preserved, not silently disabled or declared safe.
Explicit `remove` restores unchanged Brain-owned entries to their prior values;
`detach` leaves their rules in place and drops management ownership. A registered
unsupported CLI opt-in blocks lifecycle changes that require reconciliation;
remove or detach it explicitly before retrying. Neither operation removes
unowned manual rules, and restoring a pre-existing allow is not security hardening.
If an abandoned transition is pending, `recover` first proves its owners are no
longer running and releases the transition marker. When an unsupported surface
prevents reconciliation, it leaves policy unchanged and reports incomplete recovery;
then explicitly remove/detach that surface and repair the remaining opt-ins.
Codex MCP and Claude approvals remain independently selectable. Contributor probe
`tests/capture_approval_policy.py --codex-quoting` records native behaviour; a
successful probe process alone is not support certification.

Codex MCP transport must already exist. Configure or migrate transport separately.
Brain preserves other server fields, output limits, availability controls, manual
asks/denies and unrelated settings. Supported TOML table layouts include quoted
headers and inline tool tables; a fully inline root `mcp_servers={...}` layout is
refused rather than rewritten unsafely. JSON rule ordering and unrelated duplicates
are preserved. Codex full-line comments are retained; inline formatting may change.

Claude shared project allows require workspace trust. Brain never grants that
trust. Untracked local allows can load without it. Codex project configuration
also depends on host trust. Native higher-priority settings can still override
the managed set. Recognised restrictions are reported as `overridden`; broader
or external policy still requires host verification. A file being `current` only
means its on-disk managed projection is current.

Restart Codex after rule-file changes. Verify Claude settings in a fresh session.
Brain proxy refresh/restart is not a host-policy reload; Brain never automatically
reconnects or terminates the client to activate approvals.

## Ownership, migration and recovery

The machine-local ledger beside MCP registration state is
`client-approvals.json`; it does not sync with a vault. It records separate
client/scope/surface opt-ins, exact owned values, original values or absence,
last-applied values, exclusions, migration receipts and contract fingerprints.
An equal pre-existing rule is **not** ownership.

Use `approvals configure` with an `action`:

- `configure`: opt in and reconcile selected surfaces.
- `repair`: update existing opt-ins only; never enrol an unselected surface.
- `adopt`: deliberately adopt exact matching identities returned by inspection,
  supplied in `adopt_items`. Modified rules must match current desired policy
  before re-adoption; Brain does not replace a user's restrictive choice.
- `detach`: drop management and leave native settings as they are.
- `remove`: restore originals/remove generated items only while unchanged.
- `recover`: inspect pending transaction evidence and reconcile actual committed
  inventory after an interrupted operation. Live transitions cannot be recovered.

A manually deleted rule becomes an exclusion. Supply its exact identity in
`restore_items` with `repair` or `configure` to restore it deliberately. A missing
whole policy file is not recreated by routine repair. Removing a missing file's
receipt does not recreate the file. Conflicting edits remain in place and need
explicit user resolution or detachment.

Previously recorded Codex CLI migration receipts retain exact entries' original
position/count in `rules/default.rules`. Removal restores them only while their
source absence is still owned. Other lines are untouched. New CLI inspection and
adoption are blocked by the native limitation above; retained parser and migration
machinery is not a supported bypass. Grouped alternatives, raw scripts and other
unproven forms require manual review, never silent adoption. A generated-looking
comment is not consent.

Supported install/register/remove/version and MCP configuration transitions share
the policy reconciler. Owned tightening is written before exposure of the changed
target; new allowances follow target commit. Concurrent transitions defer expansion
until all complete. The global CLI distribution replacement participates too.
Doctor reports stale projections, recognised restrictions and recovery evidence.
Ambiguous or unavailable registry targets block unsafe reconciliation rather than
turning an incomplete inventory into an empty set.

Writes reuse the host registration lock and dependency-checked `FilePlan`. A
bounded `client-approvals.pending.json` journal and transition-owner locks preserve
recovery evidence. Journal creation belongs to the approval transaction, not its
immutable policy inputs; direct and nested CLI replacements still reject pending
recovery journals and intervening native edits. Individual atomic writes do not
make the whole transaction crash-atomic. Recover with all affected
client/scope/surface selections; recovery
refuses intervening user edits and arbitrary destinations. Keep recovery evidence
until the operation is resolved. It may contain private native configuration;
new journal files are owner-readable/writable only. Recovery spanning multiple
scopes restores only the explicitly selected native
layout on each invocation. The journal reports remaining targets and retains
ownership/transition state until the final affected scope has been recovered.
Recovery is a whole-native-file operation. Claude stores MCP and CLI rules in the
same settings file, so recovering that file requires selecting both surfaces;
this does not opt either surface into management.

Codex transport removal refuses a subtree containing co-located policy/settings.
Remove managed approvals first and deliberately relocate/remove remaining manual
settings before removing that transport. Removing one Brain must not erase shared
user approvals still used by other registered Brains.

## Verification references

Focused policy/ownership/lifecycle tests run in `make test`. The optional
`tests/capture_approval_policy.py` harness exercises installed native clients with
temporary homes, harmless stubs and a localhost model server; it does not adopt
the developer's real settings. Use `--rules` for Codex native prefix checking,
`--codex-shell` for actual fresh-session rule loading, and `--shell` for Claude
shell matching. Native Windows/Linux CI remains a separate
post-push gate, not something inferred from a local macOS run.

Native semantics: [Codex MCP settings](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Codex rules](https://learn.chatgpt.com/docs/agent-configuration/rules),
[Claude permissions](https://code.claude.com/docs/en/permissions) and
[Claude settings/trust](https://code.claude.com/docs/en/settings).
