Feature: Workspace-aware semantic mutations
  Workspace policy supplies defaults without silently adopting existing artefacts.

  Scenario: Create uses the bound workspace and local parent override
    Given a bound workspace with shared and local mutation policy
    When I create an artefact using startup context
    Then its membership is the bound workspace
    And its parent is the local default
    And it has both shared and local policy tags

  Scenario: Editing a global artefact applies tags without adopting it
    Given a bound workspace with shared and local mutation policy
    When I semantically edit an existing global artefact
    Then it remains unscoped with no parent
    And it has both shared and local policy tags
