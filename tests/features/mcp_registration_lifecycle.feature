Feature: Registration-driven MCP lifecycle
  Explicit integration intent and exact ownership determine managed projections.
  Behavioural coverage lives in test_mcp_registration_parity.py,
  application/test_launcher_mcp_owners.py and application/test_mcp_stable_bootstrap.py.

  Scenario: A shared user connection does not require a selected Brain
    Given a checked installed CLI with a valid absolute base Python
    And no selected or default Brain
    When the operator explicitly configures all supported user clients
    Then each supported user projection invokes the stable installed CLI
    And machine ownership records the resolved client set
    And no Brain binding or default is created

  Scenario: Repair restores intent without adding clients
    Given a canonical Claude user registration with its native projection missing
    And no Codex registration intent
    When the operator repairs user registrations
    Then the Claude projection is restored
    And no Codex projection is created

  Scenario: Layered repair composes registered dependencies
    Given two registered Brains with admitted project integrations
    And an admitted shared user connection
    When the operator requests machine-wide repair
    Then each Brain uses its own runtime contract
    And shared runtime and user projection work is planned once
    And incomplete ownership stops admission before native writes

  Scenario: Removing a transport preserves a needed bootstrap without recreating intent
    Given an owned Claude project transport and a surviving shared user route
    When the project transport is removed
    Then the target hook and bootstrap remain owned while needed
    When the target runtime changes and registrations are repaired
    Then the retained hook follows that runtime
    And the removed project transport is not reinstalled

  Scenario: Interrupted legacy migration retains recoverable evidence
    Given exact surviving legacy ownership and matching native projections
    When migration is interrupted after recording its before and after states
    Then migration resumes only from those admitted states
    And conflicting edits are preserved for explicit resolution
    And retired runtime references remain protected until normal MCP launch succeeds

  Scenario: Destructive maintenance cannot drop integration coverage
    Given a registered Brain with surviving integration ownership
    When its registry identity is removed directly
    Then removal is refused without changing its registry row
    Given an unowned native Brain entry in a registered target
    When the Brain is uninstalled
    Then uninstall is refused before deleting its Core

  Scenario: Client approvals are not transport ownership conflicts
    Given recorded transport and native client-owned approval policy
    When migration, configuration or repair updates the owned transport
    Then all approval settings remain unchanged
    And diagnostics compare ownership without claiming client approval policy
    And changed transport fields still stop admission before writes

  Scenario: Approval setup does not create transport formatting drift
    Given an owned Codex transport with unchanged command, arguments and environment
    When managed approval configuration uses an equivalent TOML representation
    Then registration diagnostics remain current
    And transport repair leaves the native configuration byte-for-byte unchanged
    And a genuinely changed transport still requires repair

  Scenario: A committed upgrade reports incomplete MCP reconciliation
    Given a Core and CLI cutover that has committed
    When MCP migration refuses a conflicting transport
    Then the direct upgrader reports a partial outcome and exits nonzero
    And the upgraded Core remains installed with recovery evidence

  Scenario: CLI replacement composes with managed approval transitions
    Given existing managed Codex MCP and Claude MCP and CLI approvals
    When a checked CLI distribution is replaced directly or within a Brain upgrade
    Then creation of its own recovery journal does not invalidate the cutover
    And each completed transition retires only its own marker
    And unchanged native approval policy is preserved
    And pre-existing recovery journals and concurrent native edits still stop replacement

  Scenario: Persisted launch survives a hostile noninteractive environment
    Given a persisted user command with an absolute checked bootstrap
    And empty PATH with foreign Python and development overrides
    When a bound workspace starts the persisted command
    Then a normal MCP read reports that workspace's selected Brain
    And startup does not install dependencies or repair registrations
