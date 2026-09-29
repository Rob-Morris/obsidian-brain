Feature: Actionable MCP tool rediscovery after Core refresh
  Core readiness and host tool-contract discovery are separate observations.
  Behavioural coverage lives in test_mcp_proxy_refresh.py and
  mcp_server/test_production_proxy_protocols.py.

  Scenario: Changed discovery tools retain an independent recovery path
    Given a connected host that has discovered the Brain tools
    When Core refresh changes command_list and command_describe contracts
    Then Core reports current while tool rediscovery reports required
    And changed calls are refused with no effects and no automatic retry
    And brain_proxy_status names host-owned tools/list as the recovery operation
    And recovery explains host refresh or user reconnect when the agent cannot trigger it

  Scenario: Refreshing Core or reading an unchanged tool is not rediscovery
    Given changed tools awaiting host rediscovery
    When an unchanged tool succeeds and Core refresh is requested again
    Then changed tools remain pending and guarded
    And Brain application discovery cannot acknowledge the host catalogue

  Scenario: Paged host discovery restores the affected calls
    Given added, changed and removed tool contracts
    When the host calls tools/list and follows every nextCursor page
    Then each returned current contract is acknowledged
    And removed contracts retire at the end of the listing
    And pending discovery clears
    And a fresh call using an affected current contract succeeds

  Scenario: Runtime recovery takes priority
    Given a runtime restart requirement and pending tool rediscovery
    When the agent requests proxy status
    Then the next action names runtime recovery first
    And pending host discovery remains visible separately

  Scenario: Malformed calls are not stale contracts
    Given a current tool contract
    When a call has a malformed envelope, reserved proxy metadata or a never-advertised tool name
    Then the proxy reports a JSON-RPC error before dispatch
    And the agent is not told to rediscover tools

  Scenario: A missing Core interface is not a host discovery problem
    Given a child whose command-interface header was rejected
    When the agent calls a Brain tool
    Then the call returns server_interface_unavailable with proxy status
    And the agent is not told to rediscover tools
