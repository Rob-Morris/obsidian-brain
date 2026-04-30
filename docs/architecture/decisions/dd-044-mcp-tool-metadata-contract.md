# DD-044: MCP tool metadata contract

**Status:** Implemented (v0.33.0)
**Extends:** DD-010

## Context

Brain's MCP tools are consumed through generated metadata, not through source
inspection. MCP clients and discovery layers such as ToolSearch see the tool
name, the tool-level description, and the generated `inputSchema`; they do not
reliably preserve long function docstrings intact.

That became a real failure mode during the `brain_edit` rollout. The parameter
type information was present in the schema, but the prose explaining what
`scope` meant, when it was required, and how its valid values differed by
target kind lived only in a long docstring section. When a client truncated the
docstring before that section, the model effectively lost the parameter
contract and could not recover from the terse validation error alone.

At the same time, the repo needed a clear ownership boundary for documenting
this rule. A Brain design note in the vault captured the investigation and
rollout, but that is out-of-repo context. The repo's documentation system must
be self-contained: current implementation rules belong in the living
documentation layer, while decision records preserve why the rule was adopted.

## Decision

Brain adopts an explicit MCP tool metadata contract.

- The living current-state contract lives in
  `docs/functional/mcp-tools.md` under `Tool Metadata Contract`.
- This DD records the rationale and architectural boundary for that contract;
  it is not the source of truth for the current implementation wording.
- For all `@mcp.tool()` registrations in
  `src/brain-core/brain_mcp/server.py`, load-bearing per-parameter semantics
  must live in generated schema descriptions (`Field(description=...)`), not in
  tool-level docstring parameter manuals.
- Fixed-choice values and stable nested shapes should be encoded structurally in
  generated schema where possible (for example via enums, `Literal[...]`, or
  explicit nested models), so truncated prose does not erase the contract.
- Tool-level docstrings stay summary-only: what the tool does, when to use it,
  key behavioural invariants, and what it returns.
- When a load-bearing validation rule is rejected at runtime, the error surface
  shown to MCP clients must name the parameter, the trigger condition, and the
  valid values or required condition so the caller can self-correct.
- Tests that assert the MCP metadata contract cite the functional-layer doc, not
  this DD.

## Alternatives Considered

### 1. Use the DD as the sole contract source

Rejected. A DD is a historical decision record. It explains why a rule exists,
but it is not the living source of truth for today's implementation wording.
Tests that cite only a DD become fragile if the decision is later extended or
superseded.

### 2. Treat contributor documentation as the canonical home

Rejected. This contract governs the MCP surface emitted by the implementation,
not just contributor workflow. Contributor docs may link to it, but the rule
belongs with the functional MCP contract.

### 3. Keep parameter guidance in tool docstrings

Rejected. Long docstrings are truncation-prone in real MCP clients and are not
reliably mapped into per-property schema descriptions. The generated schema is
the durable delivery surface for parameter semantics.

## Consequences

- `docs/functional/mcp-tools.md` now owns the current MCP metadata contract as a
  living implementation document.
- This DD preserves the reasoning behind the contract without competing with
  the functional layer for ownership of the current wording.
- `tests/test_mcp_tool_contract.py` can now participate cleanly in the
  three-way reconstruction bar: code, functional docs, and tests all point at
  the same current-state contract.
- Future changes to the metadata contract should update the functional doc and
  tests together; if the rationale or boundary changes, write a new DD that
  extends or supersedes this one.
