Feature: Explicit complete workspace adoption
  Relationship tags do not assign membership; recursive intent includes historical ownership.

  Scenario: Adopt a complete owned subtree
    Given an unscoped owner with terminal, temporal and archived descendants
    When the owner is recursively adopted into the bound workspace
    Then every owned record has explicit destination membership and policy tags
