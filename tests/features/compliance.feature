Feature: Vault compliance checking

  Scenario: Detect a temporal file outside a month folder
    Given a compliance vault with a compiled router
    And a stray temporal file "_Temporal/Logs/stray.md"
    When I run compliance checks
    Then the compliance findings include check "month_folders" for "_Temporal/Logs/stray.md"
    And the compliance summary has at least 1 warning
