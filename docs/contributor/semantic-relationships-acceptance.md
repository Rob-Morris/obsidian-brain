# Semantic relationships: implementation evidence

Checkpoint dated 2026-10-19, against approved `design/semantic-relationships`
(revision `sha256:c3ca87d3906f1efa2b1327b30f1a3897b1f3f140bd61b18485a987ded79fe516`).
All execution used disposable native command-vault fixtures. No personal vault
upgrade, vault metadata mutation, commit or push was performed. Main owns final
serial `make test`, independent whole-design acceptance and two sequential Sol
reviews. This ledger records behavioural evidence, not independent review sign-off.


## Current qualified source cut — 20 October 2026

The accepted schema cleanup is implemented and focused checks pass. The full
application inventory remains above the user-confirmed **20,000-token target**:
21,465 Claude / 21,501 Codex. The capture explicitly reports that excess and a
separate exact measured regression baseline; passing regression checks is not
product-target compliance or current-harness acceptance. The hard 3,072 per-tool
maximum remains enforced. Main owns the independent cleanup delta review, final
serial full suite and authorised commit. Review of all supported harnesses and
actual loading/limits follows that commit by the user's instruction. No commit,
push, full suite or personal-vault mutation was performed by this implementation
owner. Historical runs and earlier unresolved-gate descriptions below remain as
evidence; this source-cut qualification and the final cleanup section supersede
their status statements.

## Architecture and storage decision

The selected implementation uses independent shared JSON records under
`_Relationships/{assertion,occurrence,endpoint,event,contract,operation}` and local
semantic definitions under `_Config/Relationships/{predicate,event-type,policy}`.
SQLite under `.brain/local/relationships.sqlite` is a disposable locator/adjacency
projection; source records remain authoritative. The selected vault bounds both
shared records and the local index, including symlink ancestors. Connections close
explicitly on successful and exceptional exits.

`_relationships` owns typed endpoints, logical/contract revisions, occurrence and
dependency rules, selected history, policies and storage. `_application/relationship`
and `_application/history` integrate typed requests with the existing command
registry, permissions, preparation/admission, shared mutation lock and receipts.
Existing native document/lifecycle/workspace owners remain authoritative and compose
semantic plans before mutation. Import-boundary enforcement includes `_relationships`.
No lower package imports an application owner.

Independent records were selected for direct identity ownership, limited rewrite
scope, straightforward native FilePlan integration and easier independent conflict
handling. This is a simplicity decision supported by measured mechanics; it is not
a benchmark verdict against unmeasured per-artefact collections. Stable assertion
identity, assertion meaning, definition contract, slot state, native document and
physical container revisions are distinct. Equal triples retain separate IDs.

Unresolved operation journals retain captured inputs. Success or safe abandonment
replaces them with a small terminal receipt; completed repair needs no payload and
cannot replay cleaned events. No-op/body-only publication leaves semantic journals
and graph generation unchanged. Terminal receipts still accumulate one small file
per semantic publication; there is no automatic receipt pruning or secure-erasure
claim.

## Acceptance map

Names below are exact pytest functions. File aliases refer to
`tests/application/test_relationship_<alias>.py`, except `upgrade`, which is
`tests/test_relationship_upgrade.py`. Parameterised cases are included in the
reported test totals. Each row states what the evidence establishes; it does not
claim every possible interleaving was tested.

| Row | Implemented contract and actual behavioural evidence |
|---|---|
| A1 | Independent assertion IDs and metadata without changing the base revision: `commands::test_equal_triples_have_distinct_identity_and_external_metadata_does_not_change_revision`. Replay compares identical logical records after native changes. |
| A2 | Generic metadata on artefacts/assertions, inherited scope and native-owner refusal: `compatibility::test_metadata_about_assertions_inherits_workspace_within_and_across_batches`, `test_document_status_patch_routes_to_native_owner_with_disposition_and_history`, `test_reserved_predicates_cannot_author_competing_native_claims`. |
| A3 | Read-only subject queries include current native facts by default without allocating occurrence IDs: `native::test_subject_query_reads_native_owner_facts_without_materialising_identity`; temporal membership: `compatibility::test_temporal_relationships_and_native_occurrences_use_actual_workspace`. Broad queries explicitly leave unmaterialised native subjects unevaluated. |
| A4 | Rekey/temporal rename/convert/delete/reference continuity: `native::test_rekey_preserves_assertion_and_native_parent_occurrence_identity`, `test_temporal_rename_preserves_reference_and_conversion_dispositions`, `test_historic_events_do_not_become_current_obligations_on_native_delete`, `test_deleted_native_key_can_bind_new_metadata_without_transferring_old_identity`, `test_valid_native_artefact_without_created_accepts_metadata_without_content_rewrite`; typed custom payload reference rekey: `recovery::test_custom_typed_payload_reference_follows_rekey_without_event_revision_change`. Optional created markers are not birth authentication. `second_review::test_naming_migration_updates_binding_and_metadata` and `test_repair_captured_event_follows_rekey` cover naming migration and captured-event repair after rekey, preserving logical revisions. |
| A5 | Stable occurrence identity through rekey and fresh identity after withdrawal: `native::test_reparent_a_b_a_retires_old_occurrence_and_requires_annotation_disposition`, `test_rekey_preserves_assertion_and_native_parent_occurrence_identity`; `second_review::test_shaping_transition_disposition_is_applied` verifies shaping retires the occurrence and its explicit metadata disposition. |
| A6 | Single-value empty-slot admission and exact replacement disposition: `commands::test_single_empty_slot_admission_prevents_competing_creates`, `test_replacement_requires_explicit_revision_bound_dependency_disposition`. Two real command writers compete under native coordination. |
| A7 | Separate, attributed conflicting assessments with no native lifecycle mutation: `policies::test_conflicting_current_claims_remain_independent_without_native_status_changes`. Policy obligations are explicitly selected, not inferred from type/status. |
| A8 | Persisted evidence/document/criterion/definition pins and unknown-on-staleness: `policies::test_persisted_evidence_change_or_archive_cannot_satisfy_policy[body]` and `[archive]`, plus `compatibility::test_external_meaning_change_cannot_hide_behind_stale_indices` and its assessed fixture. `policies::test_scoped_and_paged_checks_never_promote_partial_policy_inputs` prevents partial positive standing. `second_review::test_assessed_predicate_constraint_change` and `test_assessment_requires_explicit_assessed_predicate_contract_pin` require the assessed assertion's predicate contract, including constraint evolution. |
| A9 | Native definition registration, typed constraints, semantic contract revisions and upgrade preservation: `commands::test_boolean_is_not_numeric_enumeration`, `test_old_event_pins_original_contract_after_constraints_change`; `upgrade::test_upgrade_preserves_local_definitions_and_history_and_exposes_collision`. Definition documentation edits are excluded from contract meaning by the implementation; this row is not a claim of exhaustive definition-edit coverage. |
| A10 | Retained pinned event contracts versus present-day conformance: `commands::test_old_event_pins_original_contract_after_constraints_change`; upgrade collision test preserves source bytes. |
| A11 | Selected native transitions, correction/structural changes, body/no-op exclusion: `native::test_archive_unarchive_preserves_typed_subject_and_records_only_real_transitions`, `test_bound_body_and_noop_leave_semantic_generation_and_recovery_records_unchanged`; `compatibility::test_document_noop_and_body_edit_preserve_annotated_status_without_history`; integrated packing fixture exercises real selected capture. |
| A12 | Failure outcomes, missing publication, no newer-state replay, safe abandonment, terminal compaction and SQLite lifetime: `native::test_failed_native_event_publication_repairs_captured_event_once`; `recovery::test_index_failure_reports_partial_then_reconciles_without_duplicate_event`, `test_rolled_back_state_journal_is_not_repaired_as_completed`, `test_abandon_refuses_native_committed_state_after_event_failure`, `test_repair_captured_committed_event_after_newer_native_state_does_not_replay`, `test_terminal_journals_drop_captures_and_typed_cleanup_cannot_replay_payload`, `test_real_sqlite_connections_close_after_success_and_exception[False]` and `[True]`. `second_review::test_repair_removal_projection`, `test_repair_removed_definition_preserves_later_recreation`, `test_native_executor_publication_partial_repairs_without_native_replay` and `test_native_executor_post_commit_failure_keeps_honest_outcome_and_recovery` cover both executors, removal repair and newer-source preservation. Not every filesystem crash point is fault-injected. |
| A13 | Exact bounded dependency closure and new-dependant admission refusal: `native::test_native_preparation_refuses_new_dependant_before_first_write`, `test_reassign_parent_and_membership_accepts_one_unioned_disposition`; replacement test also exercises required metadata disposition. |
| A14 | Direct original read exposes correction; concurrent head CAS and fork/cycle ambiguity: `commands::test_original_read_shows_corrections_and_deleted_correction_does_not_revive_it`; `recovery::test_two_corrections_cannot_share_one_admitted_head`, `test_imported_correction_ambiguity_cannot_revive_original[fork]` and `[cycle]`. External corrections outside observed adjacency need explicit integrity maintenance. |
| A15 | Explicit typed cleanup leaves an unresolved removed correction and removes payload from terminal operation records: `commands::test_original_read_shows_corrections_and_deleted_correction_does_not_revive_it`; `recovery::test_terminal_journals_drop_captures_and_typed_cleanup_cannot_replay_payload`. Invocation receipts/backups are outside logical shared-record cleanup. |
| A16 | >5,000-record rebuild continuation, mutation gates, targeted/paged checks and query/catalogue pagination: `budgets::test_missing_projection_larger_than_max_page_rebuilds_and_scoped_checks_finish`, `test_rebuild_continuation_rejects_changed_inventory`, `test_query_pages_beyond_one_work_budget_without_skipping_assertions`, `test_query_byte_cap_cursor_keeps_the_first_unreturned_record`, `test_definition_pages_cross_work_budget_and_preserve_builtin_overlay`; partial policy test above. Integrated native batch reconciles once. See limits below. |
| A17 | Caller events validate typed payloads and remain separate from current-state ownership: `recovery::test_custom_typed_payload_reference_follows_rekey_without_event_revision_change`; event-contract test and integrated packing workflow. |
| A18 | Integrated selected-backend W1/W3 workflow and actual native publication replay: `packing_gate::test_integrated_native_publication_packing_and_growth[independent]` and `[collection]`; native upgrade preservation test. Alternative packing mechanics and limitations are explicitly separated below; engineering gate calibration remains main-owned. |
| A19 | Supported commands capture selected history; external edits receive integrity diagnostics, not fabricated events: `compatibility::test_invalid_external_record_is_reported_and_cannot_establish_standing`, `test_external_meaning_change_cannot_hide_behind_stale_indices`; imported correction fork/cycle tests. Native generic frontmatter ownership refusals preserve the existing supported contract. |

## W1/W3 integrated persistence measurements

Command: `.venv/bin/pytest -q -s tests/application/test_relationship_packing_gate.py -k integrated`.
Latest observed result: **2 passed, 2 deselected in 12.97s**.

Both runs execute the same 28 native calls: design/plan/transcript/report creation,
local predicate registration, exposure and occurrence annotation, seven-assertion
batch with metadata, direct/warm reads, reference-preserving native rekey, native
status transition with dependency disposition, custom event, correction, direct
corrected read, transcript reciprocity check and history usage read. Both produce
13 operation receipts, four events (3,027 serialised event bytes) and 14 projection
reconciliations (initial empty projection plus 13 publications). The batch itself
reconciles exactly once.

| Measurement | Independent-record replay | Global per-kind bucket replay |
|---|---:|---:|
| Final replay files | 47 | 8 |
| File writes | 96 | 83 |
| Bytes written | 109,592 | 206,038 |
| Replay write time (ms) | 104.651 | 161.636 |
| Replay full read-back (ms) | 3.892 | 0.738 |
| Native workflow excluding replay time (ms) | 958.193 | 1,072.878 |
| Batch create including replay (ms) | 67.176 | 113.202 |
| First production direct read (ms) | 3.204 | 2.915 |
| Median warm production direct read (ms) | 3.457 | 2.930 |

The variable named `cold_create_ms_including_replay` measures creation, **not cold
reading**. First reads are not OS-cache-cleared or fresh-process measurements.
These are single local runs and include scheduling noise; no production latency
threshold is inferred.

The replay receives identical native-generated records including storage envelopes,
then verifies full persisted read-back against the selected backend, equivalent
projection tuples, updates, and a changed container revision after native rekey.
This last check proves stale packing-token detection, not an alternate native
concurrent-writer admission protocol. Production empty-slot and correction-head
races are tested separately on the selected backend.

The `[collection]` variant is **global buckets by record kind**, including separate
config and operation kinds. It is **not per-artefact affinity packing**. Neither its
write amplification nor its read timing establishes the costs of per-artefact
collections. Native lifecycle/history/index owners always execute against the
production independent backend; replay is not a second authoritative implementation.
No second production backend was built merely for this comparison.

Earlier mechanism-only probes used synthetic history/annotation data and omitted
native lifecycle/reference/projection costs. They remain in the test file as
mechanical evidence, but do not close W1/W3. Earlier integrated measurements before
terminal journal compaction were 129,483 versus 682,259 bytes; those runs also lacked
the final uniform-envelope/read-back comparison and are not substituted for the
latest table. Independent records are recommended on native simplicity and bounded
rewrite ownership, with measured costs above and the alternative's limits intact.

## Remaining review limits

- Queries certify visited projected inputs, not absent/unvisited external records.
  Subject native facts use current source observations; broad native scope remains
  explicit and unevaluated beyond materialised occurrences.
- Refresh bounds record reads and retained memory per page; filename enumeration
  remains additional O(n) rebuild work and repeats across pages. It is a usable
  continuation route rather than a permanent 5,000-record vault ceiling.
- A completed scoped check certifies selected inputs. Paged/scoped policy evaluation
  never returns satisfied. Whole-policy workloads exceeding one coherent evaluated
  page remain unknown; traversal and dependency fan-out beyond budgets require a
  narrower request. Cross-page observations are not a coherent graph snapshot.
- Recovery is a staged publication protocol, not distributed or crash-atomic
  transactions. Safe abandonment requires exact unchanged before-state proof.
  Partially changed native state without a committed marker may require explicit
  operator restoration before repair/abandonment can be proved safe.
- Optional created markers guard observable changes only. Unobservable external
  delete/recreate sequences cannot be authenticated. New supported key reuse gets a
  distinct binding after old active dependencies are dispositioned.
- Main's full serial suite, whole-design review and both Sol passes remain required.
  The per-row tests are evidence for that review, not a declaration that it occurred.

## Verification runs

Final focused result: **87 passed in 60.60s**. Exact command:

```bash
.venv/bin/pytest -q tests/application/test_relationship_native.py tests/application/test_relationship_budgets.py tests/application/test_relationship_policies.py tests/application/test_relationship_commands.py tests/application/test_relationship_recovery.py tests/application/test_relationship_compatibility.py tests/test_relationship_upgrade.py tests/application/test_profile_migration.py tests/application/test_import_boundaries.py tests/application/test_application_registration.py
```

This passed after the no-created fixture, fresh-key binding and replay envelope
corrections. A subsequent narrow native-cursor refinement binds native facts rather
than body bytes; its focused verification passed: **2 passed, 10 deselected in 3.11s** using
`.venv/bin/pytest -q tests/application/test_relationship_native.py -k 'body_and_noop or subject_query'`.
Final repository contracts and `git diff --check` also passed.
Earlier bounded-fix runs: 46 passed in 15.24s (profiles/recovery/native, including
real SQLite lifetime); 60 passed/1 failed in 49.48s (new native query fixture wrongly
expected membership for a global artefact); 41 passed/1 failed in 27.52s (new key-reuse
test read a mutation summary as a full record); 43 passed/1 failed in 17.15s (the
no-created fixture attempted a null patch that retained the key). These test fixture
errors were corrected; they are not counted as passing runs. The initial read-back
comparison also failed both replay cases because envelopes were inconsistent;
the corrected uniform-envelope comparison produced the passing measurements above.

Shipped authority/default profiles now contain exactly 37 reader, 72 contributor,
98 maintainer, 105 operator and 106 administrator commands. Historical migration
fixtures exclude newly introduced relationship/history commands; the migration
recognises those historical exact built-ins without widening custom profiles.
The original three profile failures are preserved as exact regression expectations.

`make lint`: passed (docstring coverage 79.1%; command projection/Python API tests
14 passed in 9.93s). `.venv/bin/python src/scripts/check_repository_contracts.py
--policy development`: passed. Main previously reran the frontmatter removal
regression: 1 passed in 3.06s. No full `make test` was run by this implementation owner.

## First Sol review fixes

Main triaged all seven first-pass findings as required. The initial full suite was
intentionally stopped (exit 130); it is not a passing or completed full-suite run.

| Finding | Fix and evidence |
|---|---|
| H1 | Unclassified native OSError propagates to the established COMMAND_OUTCOME_UNKNOWN/effects=unknown handler; classified semantic publication failures remain actionable partial outcomes. Existing `test_lifecycle_post_commit_failure_is_honestly_unknown` and `test_transition_unexpected_post_commit_failure_is_honestly_unknown` are unchanged. New `test_unclassified_native_commit_failure_remains_unknown_with_repair_route` proves the retained native-pending journal can still be repaired. |
| H2 | Integrity correction adjacency uses bounded observed source records, not SQLite successor rows. `test_source_check_discovers_unindexed_correction_fork` imports a valid fork without refresh. |
| H3 | Persisted endpoint identities/references, definition arrays/field contracts and nested recovery captures validate at the decoding boundary with contextual ValueErrors. `test_malformed_nested_endpoint_has_contextual_check_and_refresh_findings` and `test_malformed_nested_definition_has_contextual_check_and_refresh_findings` exercise actual check/refresh commands. |
| M4 | Generation/selector-bound depth cursors retain a bounded traversal stack. `test_depth_query_continues_roots_and_descendants_across_work_and_byte_caps` covers small work budgets and large payload pages; `test_depth_query_broad_roots_and_cycles_do_not_duplicate_or_loop` covers overlapping roots and cycles. |
| M5 | Single-value slots use complete bounded source-family membership across check pages. `test_cross_page_single_slot_conflict_uses_source_without_refresh` finds split imported duplicates without relying on a refreshed index; `test_cross_page_adjacency_too_large_is_explicitly_unresolved` prevents clean results when that set cannot fit. |
| M6 | The projection indexes unresolved publication references to captured events; cleanup refuses them with the operation-specific repair route. Terminal compaction clears those references. `test_cleanup_refuses_unfinished_publication_then_repair_and_cleanup_finish` proves recovery followed by cleanup terminates without reviving data. |
| L7 | `test_artefact_read_owner.py::FINAL_COMMAND_IDS` now explicitly lists all 106 commands, including the 17 new owners. It is not derived from the implementation. |

New regressions are in `tests/application/test_relationship_review.py`, adapted
from confirmed Sol reproductions. Exploratory non-findings were excluded. The
local-context profile golden counts remain exact (37/72/98/105/106), with the full
five-profile authority matrix assertion retained. Nested shaping preparation now
propagates the semantic frozen inputs through its outer binding; the original
one-day-clock-change test is retained with an additional frozen timestamp assertion.

Early observed fix runs: 12 new regressions passed in 7.31s; 22 targeted existing
native/read/authority/shaping regressions passed in 3.39s; command projection and
Python API contracts passed (14 tests in 3.61s).

Final broader focused run: **116 passed in 74.94s**. Exact command:

```bash
.venv/bin/pytest -q tests/application/test_relationship_review.py tests/application/test_relationship_commands.py tests/application/test_relationship_native.py tests/application/test_relationship_compatibility.py tests/application/test_relationship_recovery.py tests/application/test_relationship_policies.py tests/application/test_relationship_budgets.py tests/test_relationship_upgrade.py tests/application/test_artefact_lifecycle_owners.py tests/application/test_artefact_transition_owners.py tests/application/test_artefact_read_owner.py tests/application/test_local_context_composition.py tests/application/test_managed_operation_preparation.py::test_shaping_start_keeps_prepared_transcript_day
```

A final slot-loop variable fix prevents local occupant IDs from shadowing the
caller's explicit check scope. Its focused verification passed: **20 passed in
22.26s**, using `.venv/bin/pytest -q tests/application/test_relationship_review.py
tests/application/test_relationship_policies.py`.

Repository contracts (`--policy development`) and `git diff --check` passed.
Main owns the second independent Sol pass and final serial full suite. No full-suite
completion is claimed from the intentionally interrupted first run.

## Second Sol review fixes

Main accepted all four findings as implementation fixes. Both earlier full-suite
runs were intentionally stopped with exit 130; neither is complete or passing.
The second review's five native probes are adapted into
`tests/application/test_relationship_second_review.py`, with fifteen parameterised
cases including failure/retry and recreated-source checks.

| Finding | Fix and exact additional evidence |
|---|---|
| H1 | `shaping.start` prepares the final target/transcript writes and uses native semantic begin/completion. `artefact.migrate-naming` uses that protocol for its actual movement plan, preserves partial publication receipts and still reconciles native indexes. Pure movement guards preserve original bytes rather than expecting an unperformed frontmatter serialisation. `test_shaping_transition_disposition_is_applied`, `test_naming_migration_updates_binding_and_metadata`, `test_native_executor_publication_partial_repairs_without_native_replay[before-semantic-state-shaping]`/`[before-semantic-state-naming]`/`[projection-shaping]`/`[projection-naming]`, and `test_native_executor_post_commit_failure_keeps_honest_outcome_and_recovery[classified-shaping]`/`[classified-naming]`/`[unexpected-shaping]`/`[unexpected-naming]` verify actual effects, truthful partial/unknown outcomes and repair without native replay. |
| H2 | Assessment definition pins explicitly include the assessed assertion's own predicate contract. The real assessed fixture writes that pin; no latest-wins or automatic approval is inferred. `test_assessed_predicate_constraint_change` and `test_assessment_requires_explicit_assessed_predicate_contract_pin` cover constraint evolution and an omitted pin. |
| M3 | Repair reconciles captured removals using current source, removing absent index rows while preserving recreated definitions. `test_repair_removal_projection` and `test_repair_removed_definition_preserves_later_recreation` verify both outcomes and idempotence. |
| M4 | Endpoint identity is indexed; missing captured events resolve current typed locators by stable identity before publication and assert an unchanged logical event revision. `test_repair_captured_event_follows_rekey` preserves identity, event revision, captured payload and current native bytes across repair and retry. |

Existing shaping/lifecycle tests now distinguish native file effects from semantic
recovery/publication subjects. They retain their exact native path and context
assertions and verify the additional actual operation/record source. The strict
shaping request projection expectation explicitly includes the new empty
`relationship_dispositions` field.

Final focused commands and results:

```bash
# 51 passed in 56.94s; includes all 15 second-review regression cases.
.venv/bin/pytest -q tests/application/test_relationship_second_review.py tests/application/test_relationship_native.py tests/application/test_relationship_recovery.py tests/application/test_relationship_policies.py tests/application/test_relationship_compatibility.py

# 7 passed in 23.76s; precise native and semantic committed-effect assertions.
.venv/bin/pytest -q tests/application/test_workspace_lifecycle_context.py::test_index_failure_returns_observed_committed_subjects_and_policy tests/application/test_workspace_lifecycle_context.py::test_mid_apply_partial_names_only_observed_paths_and_keeps_context

# 14 passed in 4.44s.
make lint-command-docs

# repository contracts: ok
.venv/bin/python src/scripts/check_repository_contracts.py --policy development
```

The broader integration run completed with **168 passed, 4 failed in 125.64s**:

```bash
.venv/bin/pytest -q tests/application/test_relationship_second_review.py tests/application/test_shaping_start_owner.py tests/application/test_workspace_shaping_context.py tests/application/test_managed_operation_preparation.py::test_shaping_start_keeps_prepared_transcript_day tests/application/test_operator_mutation_owners.py tests/application/test_workspace_lifecycle_context.py
```

Its four failures were the older lifecycle assertions treating semantic record IDs
as native paths; the subsequent seven-case targeted run above closes all four.
Earlier intermediate runs recorded 1 failed/4 passed in 5.22s (the shaping probe
expected one event rather than native-transition plus disposition) and 11 failed/30
passed in 27.42s (two new-test fixture mistakes, pure-move recovery guard mismatch,
new receipt expectations and strict request projection). Those are diagnostic
runs, not passing evidence. Direct execution of the external Sol file initially
failed collection because its sibling fixture module was outside the import path;
the adapted repository tests use the normal test harness. No tool/test was
interrupted by this implementation owner during these fixes.

All four accepted Sol2 findings are implemented and focused verification is green.
No known blocker remains from this bounded fix set. Main still owns final serial
`make test` and acceptance. The W1/W3 figures above remain the measured earlier
integrated replay, not newly measured timings for this correction set; alternate
per-artefact packing remains unmeasured and the stated engineering limits remain.

## Completed full-suite regression fixes and current release gate

Main's completed serial run returned **26 failed, 7,719 passed, 37 skipped,
2 errors in 3,866.25s**. Its log is
`/private/tmp/brain-semantic-complete-suite.log`. This supersedes the earlier
interrupted runs; it is a completed failing full suite, not a passing result.
Main released source ownership before the changes below. No full suite was run
by this implementation owner.

| Regression group | Final source/assets/test disposition |
|---|---|
| Generated route and approval facts | Regenerated `src/brain-core/command-catalogue.json` (106 application commands, fingerprint `sha256:4b0ce85669d38527af4a84272a2a1ee08e1264e6d0daf26738d4e05169c0f30a`) and core `approval-contract.json` (109 application/proxy facts). Existing approval facts are unchanged; launcher approval facts remain 34 and already match. Session/release-route and approval tests pass. |
| MCP exact goldens | Counts remain explicit: 90 full MCP tools and 36 reader tools. The protocol test contains a literal mapping for all 17 new commands; the reader protocol test contains the literal seven-command reader set. No expected value is derived from the observed result. Both stdio versions, stateless HTTP, profile ceiling, exact header and strict schema tests pass. |
| Lossless native preplanning | Native semantic planning reads each selected Markdown before-image once and passes the revision-bearing content only to its selected endpoint resolver. Generic endpoint reads remain strict. All three original BOM conversion/partial-warning tests pass unchanged. New `native::test_native_semantic_preparation_reuses_selected_lossless_source_once` asserts exactly one selected source read, retained raw-byte revision and no source rewrite; `test_generic_metadata_does_not_consume_lossless_native_source` exercises the strict generic command boundary with a pinned native router fixture. |
| Stage8 owner fixtures | Both transition tests now supply the required invocation identity/clock and complete native type metadata. The scan test initialises its disposable projection before its unchanged-snapshot baseline. Original single-read, raw revision, conversion and snapshot assertions remain intact; there is no production getattr/fake context fallback. All 14 Stage8 tests pass. |
| Text-role inventory | Ten new mutating commands have explicit non-TEXT reasons: their JSON/SQLite writes and read-only Markdown roles are stated individually. The exact classification equality remains enforced. |
| Bootstrap dotted names | Documentation labels the four native predicate names as `predicate: native.<role>`; no broad namespace ignore or weakened command-name guard was added. |
| Process/interpreter restriction | No production changes. Main's approved outside-sandbox diagnostics passed 34 cases with 1 skip in 354.39s, including the observed role/process tests. |
| Recovery guidance budgets | The completed suite's three guidance-budget failures occurred while fetching the missing tokenizer through blocked DNS, before any token assertion. With main's populated cache, all three pass. Limits 115/240 and production guidance remain unchanged. |

Final focused evidence:

```bash
# 35 passed in 4.48s.
.venv/bin/pytest -q --tb=short -o tmp_path_retention_count=50 tests/test_stage8_link_text.py tests/application/test_relationship_native.py::test_native_semantic_preparation_reuses_selected_lossless_source_once tests/application/test_relationship_native.py::test_generic_metadata_does_not_consume_lossless_native_source tests/mcp_server/test_command_interface_protocol.py tests/application/test_stage7_document_text.py::test_committed_frontmatter_conversion_warns_when_index_refresh_fails

# 225 passed in 27.32s.
.venv/bin/pytest -q --tb=short -o tmp_path_retention_count=50 tests/mcp_server/test_command_interface_protocol.py tests/mcp_server/test_mcpserver_stdio_compatibility.py tests/test_approval_policy.py tests/application/test_session_catalogue_route.py tests/test_release_tooling.py::test_command_catalogue_route_renders_from_selected_source_tree tests/test_naive_bootstrap.py::TestCommandNames tests/test_mcp_tool_contract.py tests/application/test_vault_text_mutation_sweep.py::test_catalogue_has_an_explicit_text_role_decision_for_every_mutation tests/application/test_relationship_native.py tests/application/test_relationship_second_review.py tests/application/test_relationship_recovery.py

# 7 passed, 1 failed in 2.04s: only the catalogue metadata ceiling remains red.
.venv/bin/pytest -q --tb=short -o tmp_path_retention_count=50 tests/test_granular_mcp_metadata.py tests/test_mcp_proxy_refresh.py::test_recovery_guidance_token_budget tests/test_session_core.py::test_recovery_route_is_small_and_actionable
```

Repository contracts (`--policy development`) and `git diff --check` passed.
The longer intermediate focused runs completed, including pytest old-temp cleanup:
19 passed/1 failed in 309.62s (new complete owner context exposed the minimal router
fixture's missing `frontmatter_type`), 181 passed/1 failed in 282.72s (new golden
assertion initially treated header tuple entries as a mapping), 1 failed in 161.39s
(the same minimal router), and 16 passed/1 failed in 79.60s (the new strict-read
fixture hit the earlier stale-router guard). These diagnostic failures are closed
by the final focused passes above; none was interrupted or counted as passing.
The temporary retention option avoids repeated deletion of the old full-suite
fixture tree; it changes no assertion, read budget or production behaviour.

### Pre-cleanup measured MCP metadata gate (historical)

Regenerated only the current
`tests/fixtures/command_interface_granular_mcp_projection_v2.json` through its
existing deterministic capture script. Historical captures remain unchanged.
The raw registration/capture equality, schema validity/strict descriptions, source
hashes, description length and cost-regression tests pass. Actual current costs:

| Client | Tools | Full catalogue tokens | Ceiling | Maximum tool tokens | Per-tool ceiling |
|---|---:|---:|---:|---:|---:|
| Claude Code pinned replay | 90 | 23,934 | 16,384 | 2,545 | 3,072 |
| Codex CLI pinned replay | 90 | 23,967 | 16,384 | 2,542 | 3,072 |

The sole failing test is
`tests/test_granular_mcp_metadata.py::test_supported_client_projections_obey_release_token_ceilings`.
It stops first at the full catalogue cap. Six tools also exceed the unchanged
512-token compact allowlist rule: `artefact_create` (622/607),
`document_replace-text` (647/644), `document_write-body` (609/606),
`relationship_batch` (540/548), `relationship_check` (852/840), and
`relationship_query` (570/537), shown Claude/Codex respectively. The existing
exceptions remain only `document_structured-edit` and `resource_create`.
No limit or allowlist was enlarged to make this pass.

This was the additional release-gate conflict exposed before the user's accepted
cleanup and qualified regression policy below: the old 73-tool catalogue was already 15,794 tokens for Claude.
The 17 new tools and native disposition parameters are real approved contract
surface. A memory-only standard JSON Schema factoring probe preserved descriptions
and validation but still measured 22,509/22,542 tokens. A wording-only two-word
probe still exceeded 20,000 for Claude and was not applied. No speculative metadata
projection architecture or lossy descriptions were shipped. Main subsequently accepted the qualified target/regression treatment recorded
below. A1–A19 behavioural evidence and W1/W3 storage measurements above remain
accurate; these historical values are not the current compact projection costs.

## Accepted schema cleanup and qualified metadata regression checks

User authorised the measured equivalent-schema slice, retained structured graph
cursors, removed only the unused `artefact.create` dispositions argument, and
ordered commit before review of supported harness loading/limits. Main accepted
separate target reporting and an exact no-growth baseline. The preserved spike
archive was read and SHA256 verified as
`74d49721d64d82a36aef574d71e1a6a6de79c84c497e65fbf63d9e9944404c25`.
No research prototype, opaque-cursor codec or server-held plan protocol was added.

`_application._schema_compaction` is the pure canonical projection leaf. It shares
exact repeated object shapes with request-local `$defs`/`$ref`; descriptions,
defaults and titles remain at each use. Numeric `integer` is removed only when
`number` already covers it; string type is removed only beside a non-empty
string-only enum. Otherwise identical string-discriminator branches share their
unchanged shape. Traversal visits schema positions rather than arbitrary keys:
property names and literal defaults/enum data remain intact. There is no generic
keyword-deletion pass. `_application.projection` owns the compact schema used by
all adapters; examples use its inline source. The MCP adapter retains its mechanical
role and shorter operation-consent guidance.

`WorkspaceAwareRequest` now carries only selection;
`WorkspaceDispositionRequest` adds the withdrawal capability. The 16 consuming
native requests inherit the latter and retain strict disposition decoding.
Creation keeps selection and rejects even an empty supplied disposition list.
Structured graph generation/offset/selectors/traversal fields are unchanged.
The request union, command identities, authority/profile ceilings and native
history/dependency owners are unchanged by this cleanup.

New behavioural/schema evidence in
`tests/application/test_schema_compaction.py` covers exact expansion of all 106
inline request schemas, required/full-field examples and negative controls,
unknown fields, required-field omission, typed boolean/numeric enum cases,
annotations at distinct reference sites, keyword-named properties and literal
payloads, the template/frontmatter refusal, exactly 16 disposition consumers and
three structured graph-cursor inputs. Existing described-schema traversals now
expand local references and merge their siblings instead of skipping factored
properties. Original strictness assertions remain. Native query/cursor/lifecycle
and correction/recovery suites exercise the integrated request owners.

### Current measurements and honest gate treatment

The regenerated current v2 capture exactly matches the preserved spike's accepted
slice. Historical captures remain unchanged. Whole-array costs, not summed
per-tool marginals, are:

| Client | Before cleanup | Current tokens | Saving | Product target | Target excess | Largest tool |
|---|---:|---:|---:|---:|---:|---:|
| Claude Code pinned replay | 23,934 | 21,465 | 2,469 | 20,000 | 1,465 | 1,664 |
| Codex CLI pinned replay | 23,967 | 21,501 | 2,466 | 20,000 | 1,501 | 1,661 |

There are 90 application tools. This inventory excludes the three proxy recovery
controls and does not measure loaded host context, model argument quality, latency,
billing or universal upfront cost. Current-host acceptance/loading of local
references is pending the user-ordered post-commit supported-harness review.
The historical client captures establish earlier workflows, not this new schema
representation's current-host acceptance.

The desired full-inventory target is 20,000; 16,384 was conservative headroom.
`catalogue_budget` records the target, scope, pending review and exact measured
regression baselines (21,465/21,501). Each client row records `target_exceeded`,
`over_target_tokens`, its baseline and `regression_exceeded`. Tests derive target
status from measured tokens and `max(0, tokens-target)`, so an improvement below
target passes. They prevent growth above the exact current baseline and retain
capture parity; neither is named or described as release compliance. No artificial
blanket 24,576 ceiling was introduced.

Every tool retains the hard 3,072 maximum. The reviewed 512-token exceptions now
exactly match tools above that target in at least one replay:

| Tool | Claude / Codex | Typed-contract rationale |
|---|---:|---|
| `document_structured-edit` | 1,664 / 1,661 | Structural selectors and replace/insert/delete variants |
| `document_replace-text` | 591 / 588 | Revision-bound matching and native dispositions |
| `document_write-body` | 579 / 576 | Typed content, body operations, revision and disposition controls |
| `relationship_check` | 686 / 674 | Explicit scoped assessment and transcript-policy inputs |
| `relationship_query` | 523 / 490 | Structured cursor and native-scope selectors; Claude-only excess |

Each exception has a retained reason; a union-of-client check forbids obsolete
exceptions. `resource_create` is now below 512 and was removed from the exception
set. `artefact_create` and `relationship_batch` also became compact. This is an
explicitly reviewed inventory policy with a known 20k limitation, not a claim that
the product target was achieved.

### Focused verification for this source cut

```bash
# 215 passed in 99.25s.
.venv/bin/pytest -q --tb=short -o tmp_path_retention_count=50 tests/application/test_schema_compaction.py tests/application/test_projection_contracts.py tests/application/test_python_api.py tests/mcp_server/test_granular_command_adapter.py tests/test_mcp_tool_contract.py tests/test_granular_mcp_metadata.py tests/application/test_relationship_native.py tests/application/test_relationship_second_review.py tests/application/test_relationship_budgets.py tests/application/test_relationship_compatibility.py tests/application/test_workspace_lifecycle_context.py::test_shared_inventory_covers_semantic_requests_and_excludes_maintenance tests/application/test_workspace_shaping_context.py::test_shaping_selector_version_and_projection

# 26 passed in 3.90s; includes the final data-derived target-status assertions.
.venv/bin/pytest -q --tb=short -o tmp_path_retention_count=50 tests/test_granular_mcp_metadata.py tests/test_approval_policy.py tests/application/test_session_catalogue_route.py tests/test_release_tooling.py::test_command_catalogue_route_renders_from_selected_source_tree tests/application/test_import_boundaries.py

# 14 passed in 8.95s.
make lint-command-docs
```

Earlier corrected schema run: 162 passed in 16.59s. Its initial development run
reported 160 passed/2 failed in 20.16s because the new test used a nonexistent
catalogue lookup method; those two fixture errors were corrected and are not
counted as passing evidence. All tool/test cells completed; none was interrupted.
Repository contracts (`--policy development`) and `git diff --check` passed.
No full-suite or independent-review completion is claimed for this source cut.
A1–A19 behavioural coverage and storage/backend rationale remain as mapped above;
W1/W3 timings are the earlier actual measurements, not newly measured cleanup
benchmarks. There is no known implementation blocker under the accepted qualified
regression policy; final integration review/full suite and the acknowledged
post-commit harness/product-target review remain main-owned.

### Exact cleanup-delta paths

The following 31 paths are this cleanup's source/tests/capture/docs delta. Core
route/approval assets were regenerated and remained content-identical to the
preceding qualified source cut. The complete semantic-work manifest follows.

```text
docs/architecture/decisions/dd-061-typed-command-application-boundary.md
docs/contributor/semantic-relationships-acceptance.md
docs/functional/mcp-tools.md
src/brain-core/brain_mcp/_command_adapter.py
src/brain-core/scripts/_application/_schema_compaction.py
src/brain-core/scripts/_application/artefact/archive.py
src/brain-core/scripts/_application/artefact/convert.py
src/brain-core/scripts/_application/artefact/delete.py
src/brain-core/scripts/_application/artefact/rename.py
src/brain-core/scripts/_application/artefact/reparent.py
src/brain-core/scripts/_application/artefact/reparent_children.py
src/brain-core/scripts/_application/artefact/set_key.py
src/brain-core/scripts/_application/artefact/set_naming_field.py
src/brain-core/scripts/_application/artefact/set_status.py
src/brain-core/scripts/_application/artefact/set_workspace.py
src/brain-core/scripts/_application/artefact/unarchive.py
src/brain-core/scripts/_application/document/replace_text.py
src/brain-core/scripts/_application/document/structured_edit.py
src/brain-core/scripts/_application/document/update_frontmatter.py
src/brain-core/scripts/_application/document/write_body.py
src/brain-core/scripts/_application/projection.py
src/brain-core/scripts/_application/shaping/start.py
src/brain-core/scripts/_application/workspace_context.py
src/brain-core/standards/semantic-relationships.md
tests/application/test_projection_contracts.py
tests/application/test_schema_compaction.py
tests/fixtures/command_interface_granular_mcp_projection_v2.json
tests/granular_mcp_metadata.py
tests/schema_assertions.py
tests/test_granular_mcp_metadata.py
tests/test_mcp_tool_contract.py
```

## 20 October integration diagnostics and closure

The post-cleanup serial diagnostic sweep completed with 7,842 passed, 30 failed,
37 skipped and three setup/teardown errors in 5,591.59 seconds. Its source cut was
unchanged throughout. The immutable command-vault baseline lost installed Core
files during that run; later clones inherited missing VERSION, runtime exports
and package initialisers. A fresh isolated run of the six affected late modules
passed: **140 passed, one platform skip in 237.03s**. The baseline mutation remains
under investigation; this passing isolation run does not establish its cause.

The bootstrap command-count golden is now explicitly 106. Proxy 0.10.10 is an
unreleased helper proposal adding fixed-format preflight diagnostics for timeout
versus non-zero exit, elapsed milliseconds and exit status. It retains five-second
waits, fail-closed behaviour, process-group cleanup and discarded candidate stderr.
The observed intermittent preflight refusal is not claimed fixed: a native probe
completed in 2.704 seconds, while measured compaction added roughly 19 milliseconds
to schema work and does not establish a causal startup regression.

Sol's focused review found shared stdlib mutation in the new diagnostic test.
Proxy-local dependency bindings now replace that mutation, retaining cleanup and
non-disclosure checks. The revised focused group passed **46 tests in 151.77s**.
Independent closure and the final serial gate remain main-owned. Final acceptance
results belong to the commit/handoff evidence; this is a dated pre-gate checkpoint.

## Disposable baseline age isolation

The baseline now refreshes regular copied files' access/modification timestamps
before compiling/indexing. It does not follow links, change authored dates or
bytes, or weaken the immutable content hash. Native baseline/clone checks and
aged-copy/external-symlink controls passed: **seven tests in 3.00s**.

This addresses a concrete copied-file age hazard. The observed deletion time was
03:53:55 Sydney; the installed macOS dirhelper configuration specifies a 03:35
schedule and removal after three days. Lost copied files had old source times,
while recent source files survived. No direct deletion event was retained, so
actor attribution remains a strongly supported explanation rather than a proven
process trace. No system cleanup policy or product installer was changed.

All owned diagnostic/audit processes were stopped. The final serial run will use
an exclusively owned outer temporary root; no concurrent pytest runs will target
its shared numbered-root namespace. This is test-data isolation, not a reduced
suite. Final evidence is recorded separately after the last source edit.

The fixture review identified unsupported no-follow timestamp updates on Windows
Python 3.12. Freshening now checks the runtime capability and otherwise leaves
metadata unchanged; it never falls back to following links. Native baseline/clone
validation still runs on every platform. An explicit unsupported-capability
regression joins the existing checks: **eight passed in 2.69s**. Windows support
was checked statically; no local native Windows execution is claimed.

## Exact changed-path manifest

All 125 paths below are relative to the repository root. This includes
independent contributions, review corrections and integration diagnostics.

```text
docs/architecture/bounded-contexts.md
docs/architecture/decisions/README.md
docs/architecture/decisions/dd-061-typed-command-application-boundary.md
docs/architecture/decisions/dd-090-semantic-relationships-and-history.md
docs/architecture/overview.md
docs/contributor/README.md
docs/contributor/semantic-relationships-acceptance.md
docs/functional/README.md
docs/functional/cli.md
docs/functional/config.md
docs/functional/mcp-tools.md
docs/functional/scripts.md
docs/user/README.md
docs/user/system-guide.md
docs/user/workflows.md
src/brain-core/approval-contract.json
src/brain-core/brain_mcp/_command_adapter.py
src/brain-core/brain_mcp/proxy.py
src/brain-core/command-catalogue.json
src/brain-core/defaults/command-authority.json
src/brain-core/defaults/config.yaml
src/brain-core/guide.md
src/brain-core/scripts/_application/_artefact_transition.py
src/brain-core/scripts/_application/_document_mutation.py
src/brain-core/scripts/_application/_lifecycle_mutation.py
src/brain-core/scripts/_application/_schema_compaction.py
src/brain-core/scripts/_application/artefact/archive.py
src/brain-core/scripts/_application/artefact/convert.py
src/brain-core/scripts/_application/artefact/delete.py
src/brain-core/scripts/_application/artefact/migrate_naming.py
src/brain-core/scripts/_application/artefact/rename.py
src/brain-core/scripts/_application/artefact/reparent.py
src/brain-core/scripts/_application/artefact/reparent_children.py
src/brain-core/scripts/_application/artefact/set_key.py
src/brain-core/scripts/_application/artefact/set_naming_field.py
src/brain-core/scripts/_application/artefact/set_status.py
src/brain-core/scripts/_application/artefact/set_workspace.py
src/brain-core/scripts/_application/artefact/unarchive.py
src/brain-core/scripts/_application/document/replace_text.py
src/brain-core/scripts/_application/document/structured_edit.py
src/brain-core/scripts/_application/document/update_frontmatter.py
src/brain-core/scripts/_application/document/write_body.py
src/brain-core/scripts/_application/history/__init__.py
src/brain-core/scripts/_application/history/cleanup.py
src/brain-core/scripts/_application/history/configure.py
src/brain-core/scripts/_application/history/correct.py
src/brain-core/scripts/_application/history/policy.py
src/brain-core/scripts/_application/history/query.py
src/brain-core/scripts/_application/history/read.py
src/brain-core/scripts/_application/history/repair.py
src/brain-core/scripts/_application/history/write.py
src/brain-core/scripts/_application/preparation_transition.py
src/brain-core/scripts/_application/projection.py
src/brain-core/scripts/_application/registry.py
src/brain-core/scripts/_application/relationship/__init__.py
src/brain-core/scripts/_application/relationship/_maintenance.py
src/brain-core/scripts/_application/relationship/_native.py
src/brain-core/scripts/_application/relationship/_reads.py
src/brain-core/scripts/_application/relationship/_support.py
src/brain-core/scripts/_application/relationship/_types.py
src/brain-core/scripts/_application/relationship/batch.py
src/brain-core/scripts/_application/relationship/check.py
src/brain-core/scripts/_application/relationship/define.py
src/brain-core/scripts/_application/relationship/definitions.py
src/brain-core/scripts/_application/relationship/expose.py
src/brain-core/scripts/_application/relationship/query.py
src/brain-core/scripts/_application/relationship/read.py
src/brain-core/scripts/_application/relationship/refresh.py
src/brain-core/scripts/_application/relationship/undefine.py
src/brain-core/scripts/_application/requests.py
src/brain-core/scripts/_application/shaping/_start_preparation.py
src/brain-core/scripts/_application/shaping/start.py
src/brain-core/scripts/_application/workspace_context.py
src/brain-core/scripts/_application/workspace_transitions.py
src/brain-core/scripts/_command_interface/profile_migration.py
src/brain-core/scripts/_relationships/__init__.py
src/brain-core/scripts/_relationships/checks.py
src/brain-core/scripts/_relationships/definitions.py
src/brain-core/scripts/_relationships/endpoints.py
src/brain-core/scripts/_relationships/model.py
src/brain-core/scripts/_relationships/native.py
src/brain-core/scripts/_relationships/policies.py
src/brain-core/scripts/_relationships/service.py
src/brain-core/scripts/_relationships/storage.py
src/brain-core/scripts/_relationships/upgrade.py
src/brain-core/scripts/brain_application/values.py
src/brain-core/scripts/check.py
src/brain-core/scripts/start_shaping_session.py
src/brain-core/scripts/sync_definitions.py
src/brain-core/standards/README.md
src/brain-core/standards/semantic-relationships.md
tests/application/test_artefact_read_owner.py
tests/application/test_import_boundaries.py
tests/application/test_local_context_composition.py
tests/application/test_managed_operation_preparation.py
tests/application/test_profile_migration.py
tests/application/test_projection_contracts.py
tests/application/test_relationship_budgets.py
tests/application/test_relationship_commands.py
tests/application/test_relationship_compatibility.py
tests/application/test_relationship_native.py
tests/application/test_relationship_packing_gate.py
tests/application/test_relationship_policies.py
tests/application/test_relationship_recovery.py
tests/application/test_relationship_review.py
tests/application/test_relationship_second_review.py
tests/application/test_schema_compaction.py
tests/application/test_session_start_owner.py
tests/application/test_shaping_start_owner.py
tests/application/test_vault_text_mutation_sweep.py
tests/application/test_workspace_lifecycle_context.py
tests/application/test_workspace_shaping_context.py
tests/command_vault.py
tests/features/semantic_relationships.feature
tests/fixtures/command_interface_granular_mcp_projection_v2.json
tests/granular_mcp_metadata.py
tests/mcp_server/test_command_interface_protocol.py
tests/mcp_server/test_mcpserver_stdio_compatibility.py
tests/schema_assertions.py
tests/test_command_vault.py
tests/test_granular_mcp_metadata.py
tests/test_mcp_proxy_handoff.py
tests/test_mcp_tool_contract.py
tests/test_relationship_upgrade.py
tests/test_stage8_link_text.py
```
