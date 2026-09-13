Feature: Artefact creation lifecycle

  Scenario: Create a living artefact in its type folder
    Given a configured artefact creation vault
    When I create a "wiki" artefact titled "My Test Page"
    Then the created artefact path is "Wiki/My Test Page.md"
    And the created artefact file exists
    And the created artefact result type is "living/wiki"

  Scenario: Create a temporal artefact flat under its type root
    Given a configured artefact creation vault
    When I create a "log" artefact titled "Morning Session"
    Then the created artefact path matches "^_Temporal/Logs/log~Morning Session\.md$"
    And the created artefact file exists
    And the created artefact result type is "temporal/log"
