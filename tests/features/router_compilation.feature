Feature: Router compilation

  Scenario: Compile configured living and temporal artefacts
    Given a compilable router vault
    When I compile the router
    Then the compiled router contains a configured artefact "wiki"
    And the compiled router contains a configured artefact "logs"
    And the compiled router always rules include "Every artefact belongs in a typed folder."

  Scenario: Compile a discovery shaping contract that preserves lifecycle status
    Given a compilable router vault
    And a discovery-shaped type that preserves lifecycle status
    When I compile the router
    Then the compiled artefact "people" preserves shaping lifecycle status
