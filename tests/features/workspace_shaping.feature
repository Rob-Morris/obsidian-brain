Feature: Workspace-aware shaping sessions
  Scenario: A bound shaping session creates a scoped transcript
    Given a shaping caller bound to shared and local workspace policy
    When the caller opens a shaping session
    Then the new transcript is scoped with the local parent and both policy tag sets
