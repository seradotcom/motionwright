# Testing strategy

The test pyramid separates evidence rather than counting raw test totals.

1. **Domain/schema** — closed parsing, IDs/revisions, canonical Semwright rational time, locks, canvas relations/property locks, creative proposal budgets, change validation, serialization and migrations.
2. **Storage/service** — transactional CAS, request deduplication, wrong-base rejection, crash/reopen, concurrent writers and event replay.
3. **Integrated UI** — keyboard/pointer editing, shared revision updates, semantic Canvas transforms, stored alternative selection, stale/conflict states, offline/error recovery and accessibility.
4. **Native E2E** — external client → Semwright Core/Host/Native SDK → Motionwright → persisted state, including observation scopes and bounded creative commands.
5. **Render E2E** — project intent → Semwright renderer capabilities → artifact → readback → Effects/Graph.
6. **Production** — multiple projects, variants, export/reimport and revision-ten maintenance benchmark.

## Local versus CI

The workstation is kept light. Source-policy checks, formatting and other non-building checks may run locally. Dependency installation, Rust compilation/tests/clippy, browser installation and Playwright, Tauri builds, renderer work, coverage, audit, fuzzing and large fixtures belong in GitHub Actions.

Current required CI lanes are:

- `source-policy`;
- Rust core + Native SDK consumer tests and clippy;
- Studio unit/type/build;
- Chromium browser acceptance;
- Linux desktop shell check;
- immutable Semwright Native SDK pin verification.

The heavier workflow separately owns coverage/security and release-shaped builds. Renderer/Graph/Effects lanes are added only when they execute real integrations; a placeholder is never reported as acceptance evidence.

GitHub Actions receipts must identify app SHA, Semwright pin, suite, environment and input fixtures. A skipped, cancelled or unavailable gate is not a PASS.
