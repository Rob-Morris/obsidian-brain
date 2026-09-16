Feature: Workspace-aware artefact lifecycle
  Semantic transitions retain membership, and policy references protect durable identities.

  Scenario: A key transition preserves membership and restores policy tags
    Given an artefact in a bound workspace with policy tags removed
    When its canonical key is changed
    Then its workspace membership is preserved and policy tags are restored

  Scenario: A configured default parent cannot be archived
    Given an artefact selected as the workspace default parent
    When archival of that default parent is requested
    Then archival is refused without moving the default parent

  Scenario: A scoped rename restores policy tags
    Given an artefact in a bound workspace with policy tags removed
    When its file is renamed
    Then its workspace membership is preserved and policy tags are restored
