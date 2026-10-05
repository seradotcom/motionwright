# Testing strategy

The test pyramid separates evidence rather than counting raw test totals.

1. **Domain/schema** — closed parsing, IDs/revisions, rational time, locks, change validation, serialization and migrations.
2. **Storage/service** — transactional CAS, request deduplication, wrong-base rejection, crash/reopen, concurrent writers and event replay.
3. **Integrated UI** — keyboard/pointer editing, undo, stale/conflict states, offline/error recovery and accessibility.
4. **Native E2E** — external client → Semwright Core/Host/Native SDK → Motionwright → persisted state.
5. **Render E2E** — project intent → Semwright renderer capabilities → artifact → readback → Effects/Graph.
6. **Production** — multiple projects, variants, export/reimport and revision-ten maintenance benchmark.

GitHub Actions receipts must identify app SHA, Semwright pin, suite, environment and input fixtures.

A skipped, cancelled or unavailable gate is not a PASS.
