# Behaviour specification; native pytest modules provide execution evidence.
# See docs/contributor/semantic-relationships-acceptance.md for the current test map.
Feature: Semantic relationships and selected history
  Explicit metadata connects artefacts and relationships while native owners retain authority.
  Selected command changes remain inspectable without treating history as current truth.

  Scenario: Register vocabulary before authoring direct metadata
    Given an existing design and an unregistered confidence predicate
    When I try to assert confidence directly on the design
    Then the assertion is refused without changing the design
    When I register the predicate with an identity, name and clear meaning
    And I assert confidence directly on the design
    Then the assertion has its own identity and can receive further metadata
    And the design content is unchanged

  Scenario: Native relationships are visible while their owners remain authoritative
    Given a design with a parent, a primary workspace and a lifecycle status
    When I inspect its current relationships
    Then I can distinguish its native parent, workspace membership and status facts
    And inspecting them does not author competing native claims
    When I try to author a competing reserved native relationship
    Then it is refused and the native facts are unchanged
    And changing those facts still requires their native artefact operations

  Scenario: Rekeying preserves an unchanged annotated relationship
    Given an exposed parent relationship with an assessment attached to its occurrence
    When I change the parent's key through the supported artefact operation
    Then the relationship still refers to the same parent under its new key
    And its occurrence identity and attached assessment are preserved

  Scenario: Reparenting cannot silently transfer metadata to a different fact
    Given an exposed parent relationship with metadata attached to its occurrence
    When I try to change the parent without disposing of dependent metadata
    Then the change is refused without effects
    When I explicitly disposition the dependent metadata and change the parent
    Then the old occurrence is retired and its metadata is not transferred
    When I later restore the original parent
    Then the recreated relationship has a fresh occurrence when exposed
    And the old metadata does not revive

  Scenario: Selected native changes are captured without body or no-op noise
    Given the default selected history policy and a design with an exposed status occurrence
    When I change its status through the supported artefact operation
    Then one observed status transition is available without a separate event request
    When I set the same status again and edit only the design body
    Then no additional status transition is recorded
    And the unchanged native status occurrence retains its identity

  Scenario: Inspecting an original event shows its correction
    Given an inspectable history event
    When I add a linked correction using the current head revision
    Then reading the original preserves its original account
    And visibly marks its interpretation as corrected with a link to the successor
    And history queries distinguish the effective interpretation from superseded content

  Scenario: Incomplete or stale assessment inputs cannot establish approval
    Given an explicitly selected assessment policy with current qualifying evidence
    When its evidence changes or its required inputs cannot be completely evaluated
    Then the assessment cannot report satisfied standing
    And it reports the stale or unknown evaluation without changing native status

  Scenario: External changes receive integrity checking without invented history
    Given relationships whose assertions or native facts are changed outside Brain commands
    When I check their integrity against the current source
    Then observed invalid or stale inputs are reported for explicit review and repair
    And no automatic event capture is guaranteed for those external changes
    And the check does not reconstruct unseen transitions or invent an actor or change time
