# DD-086: A dismissal needs something that can reopen it

**Status:** Proposed
**Extends:** DD-082

## Context

DD-082 gave every maintenance finding a key over a declared discriminator
and a fingerprint over declared evidence, and let a person dismiss a
judgement finding for thirty days with the promise that "changed evidence
reopens it". It then made every `error` finding with no repair family a
judgement finding, keyed by its check, its code and its file, so that no
error was left only in `vault.check`.

Most of those producers declared no evidence. A finding with no evidence is
fingerprinted by its key alone, so its fingerprint never changes, and a
dismissal of it could never reopen. For `root_files` that is harmless: the
file path is in the key, so the key alone identifies the condition, and
dismissing it means "leave this file in the root". For a per-file content
error such as `living_key_fields` it is not: the file's key may go from
missing to malformed and the dismissal keeps hiding it. For
`workspace_contract:workspace_scan_unreadable` it is wrong twice over: the
finding names no file, so every failed scan shares one key and one
fingerprint, dismissing a known broken file silently hides a different file
breaking later, and because the scan stops at its first failure everything
after that file goes unchecked. Nothing in the model distinguished these
three cases; whether a dismissal was honest depended on what each producer
happened to declare.

## Decision

A dismissal is only recorded where a re-detection could reopen it.

1. **Every per-file judgement finding carries the identity a dismissal may
   rely on.** `Identity` has three values: `subject` (a non-empty
   discriminator fully identifies the condition, and no evidence is
   declared), `evidence` (a non-empty structured evidence mapping that the
   fingerprint follows; a value may be `None`, as `{"key": None}` says the
   key is missing) and `kind_only` (neither: every occurrence shares one
   key and one fingerprint). `Identity.admits(subject, evidence)` is the
   one rule for which declared shape honours which identity; the identities
   are exclusive, and empty evidence honours none. A finding that claims
   `subject` or `evidence` is refused at construction unless its shape
   honours it, so a dismissible finding can never be built from a shape
   that cannot reopen. Findings built in process, such as the machine
   pass's, take the strongest identity their shape honours.
2. **Dismissibility follows the identity, once, for both passes.** A
   judgement group is dismissible when it is a family (its member set
   reopens it) or its identity is not `kind_only`. A kind-only group is
   claimable but never dismissible: `maintenance.dismiss` and
   `machine-maintenance.dismiss` refuse it through the shared decision rule
   as a no-effect `invalid_request` error on `key` that says the finding
   has nothing that identifies a change and names the alternatives (claim
   it, or fix it). A stored dismissal is read against the same rule and
   never quiets a kind-only group.
3. **Every family-less judgement finding the Brain checks emit is
   classified in one table.** `JUDGEMENT_FINDINGS` in `_repair_common.py`,
   beside the Brain repair table, maps each `(check, code)` to its
   identity: `subject` for `root_files`; `kind_only` for
   `workspace_scan_unreadable`; `evidence` for everything else, including
   `living_key_fields`, every other per-file `workspace_contract` error and
   the five `workspace_registry` codes. The table absorbs DD-082's list of
   deliberately-judged warning and info codes: a warning or info finding is
   a judgement finding by being listed, an error is one by severity and
   must be listed. The two registry-file codes
   (`workspace_registry_unreadable`, `workspace_registry_unparseable`) are
   `evidence`, not `subject`: their file is the one registry path per
   vault, the same shape that made the scan code kind-only, so they declare
   the structured reason (`EACCES` and the like for an unreadable file;
   `not_utf8`, `invalid_json`, `not_an_object`,
   `workspaces_not_an_object` for an unparseable one) and a file that
   breaks differently reopens a dismissal.
4. **A row is a promise; a broken promise fails safe, not closed.**
   Classification looks each family-less judgement finding up by
   `(check, code)`. A finding whose row admits its shape takes the row's
   identity. An error with no row, or a finding whose shape breaks its
   row's promise (a `subject` row with no file or with evidence, an
   `evidence` row with no or empty evidence, a `kind_only` row with
   either), is demoted to `kind_only` with the breach named on the finding:
   it stays listed at its own key, so claims survive, it can never be
   dismissed or quieted by a stored dismissal, and the dismiss refusal
   names the breach. Each breach is surfaced as a `degraded_capability`
   warning on the `maintenance.list`, `maintenance.run` and successful
   decision results, naming the `(check, code)` and the broken promise.
   Detection itself goes on, so unrelated findings and every automatic
   repair proceed. The pre-existing rule that an unknown repair scope fails
   detection is unchanged: a repair that cannot be named cannot be run.
5. **The contract test fails closed.** The repair-table test pins the
   non-default rows and scans every Python module under the scripts tree,
   not only the modules `run_checks` composes today, for finding-shaped
   dict literals (a `check` and a `severity` key), skipping only a dict
   handed to `attach_repair_guidance` directly or through a name (a family
   finding) and the two functions listed with a reason as not producers.
   A dict whose severity is not a literal, an error whose check or code is
   not a literal (or a name bound only to literals within that function),
   a `dict(...)` call with a severity, or a severity set by subscript fails
   the test. `workspace_checks` declares `ERROR_CODES` and `INFO_CODES`,
   its `report` owner refuses any other code at runtime, severity is
   keyword-only, and the test requires every `report` call site to name a
   literal code (or the binding-state codes derived from the enum) and a
   literal severity, and the call sites' codes to equal the declared sets.
   Every error code found must have a table row, and every table row must
   be emitted by some producer. A parametrised behavioural suite then runs
   the real producers over fixture vaults for every classified code, every
   ownership cause and every binding state and cause, and pins the exact
   evidence each declares.
6. **Evidence is small, structured and never prose.** The frontmatter
   parser yields only strings, lists of strings or absence, so values are
   declared as they are and the canonical JSON fingerprint fails loudly on
   anything else. `living_key_fields` declares the key value as read
   (`None` when absent, a list when empty or list-valued, the string
   otherwise), so missing, empty and malformed are three conditions.
   `workspace_reference_malformed` declares the workspace field;
   `workspace_reference_wrong_type` the hub and its type;
   `workspace_hub_invalid` the hub; `workspace_ownership_invalid` the first
   rule broken (`reference`, `parent`, `archived_parent`, `membership`,
   `parent_missing`, `workspace_mismatch`, `lineage`; the two
   `validate_ownership` rules are told apart by a typed `OwnershipRuleError`
   rather than by their wording) and the parent field, plus the child's
   workspace field where that field decides the rule;
   `workspace_policy_invalid` the shared default parent and tags. The
   binding state is an enum owned by `_common/_workspace.py`
   (`unconfigured`, `valid`, `terminal_inactive`, `configured_invalid`),
   the finding code is derived from it, and `resolve_workspace_binding`
   names which step a `configured_invalid` manifest failed (`link`,
   `brain_slug`, `alias`, `hub`, `hub_policy`, `local_defaults`), so the
   `workspace_binding_*` codes declare the bound hub and that cause, or
   `cause: manifest` with the binding error's code when the manifest
   cannot be read at all. A new binding state can never crash
   `vault.check`: its code is derived, and the contract test then demands
   a row.
7. **The transition is honest.** A dismissal recorded before a producer
   declared evidence holds the old constant fingerprint, the key. It no
   longer matches, so the finding reopens once, which is the correct
   outcome: its evidence is now declared and may differ from what was
   dismissed. Dismissing it again at the current fingerprint replaces the
   record. A stored dismissal of a kind-only finding is inert: it never
   quiets the finding, and retention prunes it. Neither case fails a read
   or a write, and no migration touches the decisions file.

## Alternatives Considered

- Failing detection on a broken promise (raise, reported as `conflict`):
  rejected. It blocked `maintenance.list`, every decision command and every
  automatic repair, including `router` and `lexical`, until a release fixed
  a Core bug the user could not resolve. Reporting it as `internal_error`
  instead was rejected for the same reason: the hazard this decision
  guards against is a finding wrongly going quiet, and demoting the one
  finding to kind-only removes that hazard while everything else proceeds.
- Letting a `kind_only` finding declare its exception message as evidence,
  so it could be dismissed and reopen on a different message: rejected.
  Prose fingerprints change on rewording and stay the same across different
  failures with the same wording, and DD-082 already rules it out.
- Giving `workspace_scan_unreadable` a file so the key identifies it:
  rejected for now. The scan raises from several places, and its first
  failure stops it, so one per-file finding would still hide the files
  after it. The honest surface is a single claimable, undismissible finding
  until the scan reports every failure.
- A flag per producer, or per check function, saying whether it is
  dismissible: rejected. The three cases are one classification with three
  values, and a flag beside each producer would drift from the finding's
  actual shape; one table with the promise enforced at detection cannot.
- Deriving dismissibility from the finding's shape at decision time, with
  no identity on the finding: rejected. The decision rule and the detection
  promise would then be two encodings of one rule, and a demoted finding
  with a file would look dismissible to the decision side.
- Declaring frontmatter values "by type" when JSON could not carry them:
  rejected once the parser was checked; it only yields strings and lists
  of strings, and a fallback would have hidden a parser change rather than
  failing on it.
- Keeping the two registry-file codes as `subject` rows: rejected; the
  argument that made the scan code kind-only applies to any finding keyed
  on one per-vault path.
- Scanning only the modules `run_checks` composes, or asserting that set:
  rejected in favour of scanning the whole scripts tree, which sees a new
  collector before it is wired in.
- Migrating the decisions file to the new fingerprints: rejected. A
  dismissal records what a person saw; recomputing it would quiet evidence
  they never looked at.

## Consequences

- `maintenance list` shows `workspace_scan_unreadable` as an open item with
  its key as its fingerprint; dismissing it is refused, claiming it works.
- Per-file `workspace_contract`, `living_key_fields` and registry-file
  findings carry `evidence` in `vault.check` output and fingerprint over
  it, so a dismissal survives a reworded message and reopens when the
  condition changes.
- An existing dismissal of one of those findings reopens once after
  upgrade; `root_files` dismissals are unaffected.
- A producer that adds a family-less error code, or changes what one
  declares, must add or change a row and an evidence case, or the
  repair-table test and the evidence suite fail. A producer that breaks its
  row in a shipped Core degrades that one finding to kind-only, with a
  warning on every maintenance result naming it, and blocks nothing else.
- `resolve_workspace_binding` returns a named tuple with a fourth `cause`
  field; its callers unpack four values and compare against
  `BindingState`, and session bootstrap still reports the state's string.
- The machine pass is unchanged in behaviour: every machine finding has a
  non-empty subject, so every one is dismissible, but the refusal applies
  there too through the shared rule.

## Verification

`tests/repair/test_repair_table.py` pins the non-default rows and holds the
fail-closed producer scan; `tests/test_maintenance_findings.py` covers
`Identity.admits`, the construction invariant, each demotion and the
structural rule; `tests/application/test_judgement_finding_evidence.py`
runs the real producers for every classified code, ownership cause and
binding state and cause, pins their evidence, and covers the
`living_key_fields`, ownership and `workspace_scan_unreadable` command
paths, the legacy-record transition and the fail-safe demotion through
`maintenance.list`, `dismiss`, `claim` and `run`;
`tests/application/test_maintenance_decisions.py` covers the refusal and
`root_files`; `tests/application/test_workspace_checks.py` pins the
registry reasons; `tests/application/test_workspace_membership_policy.py`
covers every binding cause;
`tests/application/test_launcher_machine_maintenance.py` covers the refusal
on the machine side.
