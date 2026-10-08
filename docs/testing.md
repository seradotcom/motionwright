# Testing strategy

The test pyramid separates evidence rather than counting raw test totals.

1. **Domain/schema** — closed parsing, IDs/revisions, canonical Semwright rational time, locks, canvas relations/property locks, creative proposal budgets, change validation, serialization and migrations.
2. **Storage/service** — transactional CAS, request deduplication, wrong-base rejection, crash/reopen, concurrent writers, event replay, future-schema rejection, keyset pagination, backup digest verification, dry-run import, restore generation rotation without receipt resurrection, immutable content-addressed blob admission, bounded digest-verified reads, portable bundle round-trip and tamper rejection.
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
- immutable Semwright Native SDK pin verification;
- security/delivery policy, controlled-import contracts and exact-source delivery-manifest evidence.

The heavier workflow separately owns coverage/security and release-shaped builds. Renderer/Graph/Effects lanes are added only when they execute real integrations; a placeholder is never reported as acceptance evidence.

GitHub Actions receipts must identify app SHA, Semwright pin, suite, environment and input fixtures. A skipped, cancelled or unavailable gate is not a PASS.
## Canonical evidence lane

The `Canonical Graph and Effects` workflow is exact-SHA evidence for adapter compatibility. It runs Semwright's own Project Graph adapter and Effect Conformance suites from the pinned source before Motionwright's wrapper tests. A green lane proves the consumer boundary compiles and preserves the upstream contracts; it does **not** claim Graph admission, render success, or a product-level Effects PASS for a Motionwright deliverable.

## Production job-state acceptance

Job projection tests cover cancellation request versus confirmed cancellation, terminal-state non-resurrection, reconciliation after an uncertain outcome, and CURRENT/STALE applicability against the open project revision. Browser acceptance also verifies that demo mode exposes an empty truthful ledger rather than fabricated runtime jobs. Canonical driver execution remains covered by the dedicated Native Production and renderer workflows.


## Performance evidence lane

Heavy performance evidence is produced in GitHub Actions by the Performance Acceptance Evidence
workflow. The harness uses three unrelated briefs and deterministic S/M/L project sizes. Each
dataset is seeded into the real SQLite service boundary, receives ten predefined versioned edits,
reopens from disk, checks editability and journal continuity, and records per-operation timings
plus database size and process resource usage. The workflow fails if any measured commit exceeds
its declared CI latency budget.

The report measures the application-owned derived cache separately from renderer throughput.
Each S/M/L dataset admits a fingerprint-bound preview index, reopens it through the verified
content-addressed store, records bytes checked plus validation cost and counts the exact scene
preview-index regeneration units avoided. It also mutates a delivery render input and requires a
cache miss for the changed fingerprint. This evidence does not claim avoided renderer frames,
GPU time or wall-clock render savings; renderer-level cold/warm evidence remains separate.

Human feedback remains NOT_RUN until actual participants exist. The benchmark never manufactures
comparative claims against other editors. See `docs/derived-cache.md` for cache identity,
invalidation and recovery rules.


## Variants and localization evidence lane

The Variants and Localization workflow is the exact-source regression lane for delivery derivation. It runs domain invariants for crop approval, locale voice/timing separation, protected narrative cuts and dependency invalidation; canonical Film projection for portrait/localized/cut output; Native SDK contract tests; Studio unit/type/build; and the focused Chromium delivery-variant acceptance.

That lane is deliberately separate from product-level human acceptance. A green workflow proves the implemented contracts for that SHA; it does not manufacture creative approval for an actual campaign or localized voice.
