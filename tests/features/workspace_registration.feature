Feature: Explicit canonical workspaces and folder registrations
  Canonical identity belongs to a Brain; each local folder has its own registration.

  Background:
    Given a selected Brain and two unregistered local folders

  Scenario: Registration requires an existing canonical workspace
    When the first folder is registered against a missing workspace
    Then registration refuses without folder or registry effects and names the creation command

  Scenario: First registration requires explicit canonical identity
    Given an explicitly created shared workspace
    When the first folder is registered without a slug
    Then registration refuses without inferring identity from the folder name

  Scenario: Two folders share one canonical workspace
    Given an explicitly created shared workspace
    When both folders are registered against that workspace
    And the first registration is repeated without a slug
    Then both folder registrations retain the same canonical identity

  Scenario: Unregister selects one folder
    Given an explicitly created shared workspace
    And both folders registered against that workspace
    When the first folder is unregistered
    Then only the selected folder is unlinked and the shared workspace remains

  Scenario: Rebind selects one folder and an existing target
    Given an explicitly created shared workspace
    And both folders registered against that workspace
    And an explicitly created target workspace
    When the first folder is rebound to that target
    Then the other folder and both canonical workspaces remain
