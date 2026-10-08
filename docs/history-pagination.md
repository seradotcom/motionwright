# Recent-first project history

The Changes workspace reads committed project events newest-first, so edits to a project with more than 100 revisions remain visible immediately. The same append-only journal and application-owned SQLite boundary remain authoritative.

## Backend

- Storage exposes an additional read-only `event_records_through(project_id, through_revision, limit)` keyset query: `revision <= through_revision`, descending revision order, hard-bounded to 1–256 returned rows. This query does not modify rows, create execution receipts, rewind creative state or mint Semwright runtime authority.
- The existing ascending `event_records_since`, `StudioService::history`, and Tauri `project_history` remain intact for older callers. The new service and desktop command use `history_recent` and `project_history_recent`.
- The desktop request is scoped to the supplied project ID. SQLite's canonical journal key is the project ID plus revision; pages never overlap when the next cursor is `oldest_loaded_revision - 1`.

## Studio

- The initial read requests **101** newest committed events but displays only 100, using the extra row to determine whether earlier revisions exist. This avoids pretending that exactly 100 loaded events means the journal is complete.
- **Load earlier changes** requests another maximum of 101 records through the older inclusive cursor, appends only 100 after validating strict descending order, and stops when no additional page exists.
- The view resets on project ID, generation or revision changes, and ignores stale responses from previous project contexts. Errors remain visible with an explicit retry, without deleting the previously rendered page.
- Browser-demo data follows the same newest-first semantics but remains a fixture, never production event evidence.

## Verification

Rust storage regression creates 260 real sequential SQLite revisions, verifies newest-first page boundaries, entire event coverage with no duplication, project isolation and unchanged ascending compatibility. Vitest rejects duplicated, out-of-order and out-of-bounds pagination results. Playwright runs a clearly synthetic Tauri bridge with 260 revisions, including newest-first startup, repeated older pages, retry after a transient read error, and unchanged creative revision.

This evidence tests history correctness, not reconstruction of model reasoning, independent creative review or product-level acceptance.
