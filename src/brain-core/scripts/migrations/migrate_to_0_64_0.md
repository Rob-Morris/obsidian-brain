# MCP tool-name projection

Replace exact Brain-authored root bootstrap lines referring to MCP `session.start`
with `session_start`. Custom prose and canonical command IDs in profiles remain
unchanged. The migration declares all affected bootstrap files for upgrade rollback
and refuses files resolving outside the vault. Re-running it is a no-op.

Project registration repair uses the same replacement when updating managed
bootstrap instructions. Existing clients must reconnect and discover the new
interface epoch; dotted raw tool names are not aliases.
