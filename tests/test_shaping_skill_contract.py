from pathlib import Path


SKILL_ROOT = Path("src/brain-core/skills/shaping")


def _read(relative_path):
    return (SKILL_ROOT / relative_path).read_text()


def test_shaping_root_has_portable_public_identity():
    parent = _read("SKILL.md")
    assert "name: shaping" in parent
    assert "name: shaping:" not in parent


def test_assess_reads_taxonomy_and_selects_mode_before_opening_session():
    assess = _read("references/assess.md")

    taxonomy_read = assess.index('resource.read(resource="type"')
    mode_selection = assess.index("Select the shaping workflow")
    session_open = assess.index('shaping.start(target="{path}", mode="{mode}")')

    assert taxonomy_read < mode_selection < session_open
    assert "start-shaping" not in assess
    assert "skill_type" not in assess


def test_parent_router_does_not_hardcode_shapeable_type_lists():
    parent = _read("SKILL.md")

    assert "Designs, Plans, Tasks" not in parent
    assert "People, Ideas, Cookies" not in parent
    assert "taxonomy's `## Shaping` metadata" in parent


def test_terminal_workflows_apply_taxonomy_completion_status():
    for relative_path in ("references/refine.md", "references/discover.md"):
        content = _read(relative_path)
        assert "{completion_status}" in content
        assert "artefact.set-status" in content


def test_skill_uses_granular_shaping_start_consistently():
    content = "\n".join(
        path.read_text() for path in [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").glob("*.md"))]
    )

    assert "start-shaping" not in content
    assert 'shaping.start(target="{path}", mode="{mode}")' in content
    assert "`shape`" not in content
    assert "brain_action" not in content


def test_shared_mutation_contract_names_granular_document_owner():
    assess = _read("references/assess.md")

    assert "complete propagation set through `document.edit`" in assess
    assert "mechanically narrow without narrowing the semantic scope" in assess


def test_shaping_standard_uses_public_granular_command_name():
    standard = Path("src/brain-core/standards/shaping.md").read_text()

    assert "`shape`" not in standard
    assert "`shaping.start` command" in standard


def test_refine_routes_resumptions_with_non_question_agenda_state():
    assess = _read("references/assess.md")

    assert "every other `flavour: convergent` artefact" in assess
    assert "no open agenda" in assess
    assert "candidate-exit review and status handling" in assess
    assert "-> **refine**" in assess


def test_refine_contract_is_event_driven_and_evidence_first():
    refine = _read("references/refine.md")

    for trigger in (
        "session start or resumption",
        "research or agent-work result",
        "source artefact addition or scope change",
        "four-Cs review finding",
    ):
        assert trigger in refine
    assert "Do evidence work before asking" in refine
    assert "Run the impact sweep to a fixed point" in refine
    assert "### Reconciliation R4" in refine


def test_refine_persists_decisions_work_and_lineage_in_the_artefact():
    refine = _read("references/refine.md")

    assert "## Shaping Decisions" in refine
    assert "## Shaping Work" in refine
    assert "A merge names one surviving ID" in refine
    assert "A split keeps the parent as a superseded lineage record" in refine
    assert "source-qualified" in refine


def test_refine_keeps_asked_questions_immutable_and_logs_agenda_mutations():
    assess = _read("references/assess.md")
    refine = _read("references/refine.md")

    assert "Asked-question IDs are immutable, transcript-scoped historical turn identifiers" in refine
    assert "Q4 — D11: Responsive repeated quit" in assess
    assert "Reserve full immutable source qualification" in assess
    assert "Proposal confirmations, review-finding dispositions" in assess
    assert "Q3 — D2 + D4: Ownership boundary" in refine
    assert "living/design/design-a:D4" in refine
    assert "treat it as an ephemeral candidate prompt" in refine
    assert "Do not allocate prompt IDs" in refine
    assert "authoritative chronological mutation log" in refine
    for event in (
        "`Added`",
        "`Reframed`",
        "`Split`",
        "`Merged`",
        "`Deferred`",
        "`Resolved`",
        "`Reopened`",
        "`Superseded`",
        "`Retired prompt`",
        "`Propagated`",
    ):
        assert event in refine
    assert "outcome and authority when a decision resolves" in refine
    assert "Do not duplicate the full event in a source artefact" in refine


def test_refine_persists_pending_proposals_without_event_replay():
    refine = _read("references/refine.md")

    assert "Represent each pending transformation in the owning source table" in refine
    assert "state `proposed`" in refine
    assert "originating `Rn`" in refine
    assert "On decline, restore the prior active state" in refine
    assert "must not be resurfaced unless relevant content or evidence changes" in refine


def test_refine_asks_for_one_commitment_and_reviews_proposals_sequentially():
    assess = _read("references/assess.md")
    refine = _read("references/refine.md")

    assert "One user commitment at a time" in assess
    assert "never several independent choices" in assess
    assert "## Cold opening" in refine
    assert "Do not offer several inferred resolutions for bulk confirmation" in refine
    assert "At a cold opening, always handle proposals individually" in refine
    assert "create a review queue and present only its first item" in refine
    assert "reconcile and reprioritise before presenting the next item" in refine
    assert "offer the genuinely viable alternatives as multiple choice" in refine


def test_discovery_is_open_ended_adaptive_and_audited():
    discover = _read("references/discover.md")

    for contract in (
        "open-ended refinement",
        "Refresh the thread map",
        "Retire prompts answered directly or indirectly",
        "Follow the user's energy",
        "Improve current accuracy proportionately",
        "### Reconciliation Rn",
        "Current discovery pass complete",
        "For a temporal artefact, preserve the bounded moment",
        "Conversation stop",
        "Shaping completion",
        "leave the lifecycle status unchanged",
    ):
        assert contract in discover


def test_brainstorm_reconciles_shape_and_promotes_only_mature_items():
    brainstorm = _read("references/brainstorm.md")

    for contract in (
        "Shape synthesis loop",
        "Apply every answer broadly",
        "Refresh the working shape",
        "Put evidence first",
        "Follow useful momentum",
        "Promote mature work",
        "### Reconciliation Rn",
    ):
        assert contract in brainstorm


def test_four_cs_review_is_optional_consent_based_and_repeatable():
    parent = _read("SKILL.md")
    review = _read("references/review.md")

    assert "user may bypass it" in parent
    for quality in ("Correctness", "Clarity", "Consistency", "Completeness"):
        assert f"**{quality}:**" in review
    for contract in (
        "read-only subagent when available",
        "recommend review as the next step",
        "**A. Run the [independent / separate] four-Cs review — recommended.**",
        "**B. Skip the review and [set the artefact to `{completion_status}` / hand off to refine / complete the current pass with status unchanged / exit explicit `shaping` to `{completion_status}`].**",
        "**C. Stop here without completing the current pass; leave lifecycle status unchanged.**",
        "ask for one commitment before applying any review-originated edit",
        "recommend the highest-priority reopening",
        "Only a finding the user accepts becomes a reconciliation trigger",
        "use the same recommended A/B/C offer",
        "Do not resurface a declined finding",
        "Set `review-entry` to `candidate-exit`",
        "At `mid-pass`, return directly to the active workflow",
    ):
        assert contract in review
    assert "finish/rerun/continue navigator" in review
    assert "This shaping pass looks ready" not in review


def test_four_cs_exit_is_agent_led_and_review_findings_are_sequential():
    review = _read("references/review.md")

    assert "## Agent-led offer" in review
    assert "recommend review as the next step" in review
    assert "Keep A visibly recommended rather than presenting three equally weighted paths" in review
    review_all = review.index("If the user asks to review all")
    queue = review.index("create a queue", review_all)
    first_item = review.index("explain only the first item", queue)
    reprioritise = review.index("reconcile and reprioritise before continuing", first_item)
    assert review_all < queue < first_item < reprioritise
    assert "fall back to genuinely viable multiple-choice alternatives" in review
    assert "Claiming final readiness before review or explicit bypass" in review


def test_candidate_exit_checks_the_bar_and_uses_dynamic_completion_status():
    refine = _read("references/refine.md")
    standard = Path("src/brain-core/standards/shaping.md").read_text()

    bar_check = refine.index("assess the current artefact explicitly against the taxonomy bar")
    review_offer = refine.index("use the recommended A/B/C four-Cs offer")
    status_change = refine.index("artefact.set-status")
    assert bar_check < review_offer < status_change

    assert "[artefact] is `{completion_status}`" in standard
    assert "[artefact] is ready" not in standard
    assert "set `{completion_status}` for transition" in standard
    assert "set `ready`" not in standard


def test_discovery_status_behaviour_is_explicit_and_preservation_is_non_terminal():
    assess = _read("references/assess.md")
    discover = _read("references/discover.md")
    standard = Path("src/brain-core/standards/shaping.md").read_text()

    assert "effective `status_behaviour` (default `transition`)" in assess
    assert "`preserve` leaves the current non-terminal status unchanged" in assess
    assert "do not change an existing enduring status" in discover
    assert "already in `shaping`" in discover
    assert "C stops without asserting pass completion" in discover
    assert "A preserved target in a terminal status is rejected before any mutation" in standard
    assert "complete a preserved discovery pass with status unchanged" in standard


def test_selected_discovery_taxonomies_have_purpose_specific_bars():
    people = Path(
        "src/brain-core/artefact-library/living/people/taxonomy.md"
    ).read_text()
    journal = Path(
        "src/brain-core/artefact-library/temporal/journal-entries/taxonomy.md"
    ).read_text()
    thoughts = Path(
        "src/brain-core/artefact-library/temporal/thoughts/taxonomy.md"
    ).read_text()

    assert "**Status behaviour:** `preserve`" in people
    assert "material uncertainty or inconsistency is explicit" in people
    assert "**Completion status:** `active`" in people
    assert "faithfully captures the user's meaning and voice" in journal
    assert "coherent enough to stand alone" in journal
    assert "preserve what surfaced" in thoughts
    assert "uncertainty or incompleteness may remain" in thoughts


def test_review_severity_identity_and_declined_finding_exit_are_explicit():
    review = _read("references/review.md")

    for severity in ("`blocking`", "`material`", "`minor`"):
        assert severity in review
    assert "Keep the same `Cn`" in review
    assert "only to the most recently presented finding inventory" in review
    assert "After the user declines one or more findings" in review
    assert "If a blocking finding remains true, do not claim readiness" in review


def test_review_and_completion_contracts_keep_authority_and_modes_separate():
    refine = _read("references/refine.md")
    brainstorm = _read("references/brainstorm.md")
    standard = Path("src/brain-core/standards/shaping.md").read_text()

    assert "accepted four-Cs review finding" in refine
    assert "Brainstorm never applies the artefact's completion status" in brainstorm
    assert "Reviewer output that the user has not accepted is candidate evidence" in standard
    assert "Brainstorm:** hand off to refine" in standard


def test_shaping_transcript_taxonomy_allows_generated_reconciliation_events():
    taxonomy = Path(
        "src/brain-core/artefact-library/temporal/shaping-transcripts/taxonomy.md"
    ).read_text()

    assert "### Reconciliation R4" in taxonomy
    assert "non-speaker, append-only audit event" in taxonomy
    assert "Allocate `Rn` monotonically within the transcript" in taxonomy
    assert "does not require an adjacent `### User` turn" in taxonomy
    assert "authoritative chronological mutation log" in taxonomy
    assert "Keep historical questions immutable" in taxonomy
    assert "Separate current state from event history" in taxonomy
    assert "Scope IDs to the transcript" in taxonomy
    assert "Keep candidate prompts ephemeral" in taxonomy
    assert "Joint continuation" in taxonomy


def test_shaping_transcript_change_is_additive_and_requires_no_user_data_migration():
    assess = _read("references/assess.md")
    standard = Path("src/brain-core/standards/shaping.md").read_text()
    taxonomy = Path(
        "src/brain-core/artefact-library/temporal/shaping-transcripts/taxonomy.md"
    ).read_text()

    assert "Every legacy dialogue-only transcript is valid" in assess
    assert "older `Q.` / `> A.` turns and Agent/User headings" in assess
    assert "never backfill inferred historical events" in assess
    assert "This transcript format is additive and forward-only" in standard
    assert "Every existing dialogue-only transcript format remains valid" in standard
    assert "No version-bound user-data migration, check remediation, or repair rewrite is required" in standard
    assert "Every existing dialogue-only transcript remains valid" in taxonomy
    assert "older `Q.` / `> A.` turns and `### Agent` / `### User` turns" in taxonomy
    assert "no version-bound user-data migration, check remediation, or repair pass is required" in taxonomy
