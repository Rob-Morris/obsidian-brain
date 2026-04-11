Feature: Artefact creation lifecycle

  Scenario: Create a living artefact in its type folder
    Given a configured artefact creation vault
    When I create a "wiki" artefact titled "My Test Page"
    Then the created artefact path is "Wiki/My Test Page.md"
    And the created artefact file exists
    And the created artefact result type is "living/wiki"

  Scenario: Create a temporal artefact in the current month folder
    Given a configured artefact creation vault
    When I create a "log" artefact titled "Morning Session"
    Then the created artefact path matches "^_Temporal/Logs/\d{4}-\d{2}/log~Morning Session\.md$"
    And the created artefact file exists
    And the created artefact result type is "temporal/logs"
