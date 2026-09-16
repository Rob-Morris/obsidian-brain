Feature: Vault compliance checking

  Scenario: Report an empty artefact folder under a temporal type root
    Given a compliance vault with a compiled router
    And an empty artefact folder "_Temporal/Logs/project~stale"
    When I run compliance checks
    Then the compliance findings include check "empty_folders" for "_Temporal/Logs/project~stale"
    And the compliance summary has at least 1 info

  Scenario: Reject a scoped child of an unscoped parent
    Given a compliance vault with a compiled router
    And a workspace member owned by an unscoped parent
    When I run compliance checks
    Then the compliance findings include check "workspace_contract" for "Wiki/Child.md"
