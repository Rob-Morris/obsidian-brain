# Behaviour specification; tests/application/test_result_* and tests/test_proxy_result_budget.py provide execution evidence.
Feature: Shared invocation results and informed query paging
  Each agent or CLI job owns the latest invocation of each command.
  Every response is one bounded page with counts and selection visibility.

  Scenario: Interleaved commands and concurrent agents keep independent results
    Given two result sessions using one authenticated account
    When both invoke the same command and one invokes another command
    Then their result slots remain independent
    And a later invocation replaces only its owner's slot for that command
    And a superseded ID never returns the newer invocation's output

  Scenario: Get-all obeys the same response limit
    Given a result larger than the requested page size
    When the reader requests page zero
    Then one bounded page is returned with a truncation message
    And the reader sees total pages and remaining pages within and outside the selection
    And subsequent page calls retrieve the remaining selected output

  Scenario: Source changes inform the reader without forcing a restart
    Given a lazy query whose first page has been returned
    When the query projection changes without changing the number of records
    And the reader asks for page two and then page one
    Then both pages reflect current data and independently report the change
    And unchanged repeated reads reuse cached output without repeating the warning

  Scenario: One-shot execution consent permits delivery but not another execution
    Given a specifically authorised command has produced output and spent its execution consent
    When its owner retrieves retained output under the original consent context
    Then the output is delivered without executing the command again
    When that consent is revoked or its owner is replaced
    Then retained bytes do not revive the delivery entitlement

  Scenario: Oversized output is recoverable without corrupting structure
    Given a text line or structured record exceeds one safe page
    When it is retrieved with the smallest page size
    Then Unicode-safe fragments or explicit bounded detail references make progress
    And the complete original content can be reconstructed
    And every final decorated response fits the supported byte limits
