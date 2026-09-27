Feature: Existing type identifiers are lookup synonyms
  Behavioural coverage lives in test_sync_definitions.py and
  application/test_type_library_mutation_owners.py,
  application/test_cross_adapter_parity.py and
  application/test_transition_preparation.py.

  Scenario: Select a library type using its frontmatter type
    Given library key living/notes maps to frontmatter type living/note
    When status or sync selects either identifier through MCP or the command interface
    Then the selected library entry is living/notes
    And results and sync tracking use the canonical library key

  Scenario: Resolve synonyms from existing taxonomy metadata
    Given a library type has an installed taxonomy
    When the installed taxonomy changes its frontmatter type
    Then lookup uses the installed mapping rather than guessing a singular form
    And an uninstalled library type uses its library taxonomy mapping

  Scenario: Both synonyms in a status filter select one type
    Given library key living/notes maps to frontmatter type living/note
    When a status filter includes both identifiers
    Then living/notes is returned once

  Scenario: Ambiguous or unknown lookup cannot mutate a type
    Given an identifier matches multiple library entries or matches none
    When status or sync selects that identifier
    Then the lookup reports an error
    And no selected type is modified

  Scenario: A synonym cannot redirect consent to another type
    Given operation-specific consent was prepared for a frontmatter type
    When that identifier maps to another library entry before execution
    Then execution cannot enter the newly selected operation using the old consent
    And no type is modified
