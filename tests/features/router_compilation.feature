Feature: Router compilation

  Scenario: Reject a filename pattern that escapes its artefact folder
    Given a compilable router vault
    And a taxonomy filename pattern that traverses into configuration
    When I attempt to compile the unsafe taxonomy
    Then compilation rejects the filename path before publishing a router

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

  Scenario: A lifecycle move leaves the router and listing ready for other sessions
    Given two Brain clients sharing a compiled vault
    When one client moves an idea into its terminal status folder
    Then the other client sees the moved idea and can change it without repair
