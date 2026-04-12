# Brain Bootstrap

Use this file as the bootstrap entry point only. The full session payload lives in `brain_session` JSON or the generated markdown mirror at `.brain/local/session.md`.

1. If MCP is available, call `brain_session`.
2. Otherwise, read `.brain/local/session.md` if it exists.
3. Otherwise, read `.brain-core/md-bootstrap.md` and follow the degraded fallback path from there.

`md-bootstrap.md` is the explicit raw-file fallback. Do not treat this file as a second bootstrap payload surface.
