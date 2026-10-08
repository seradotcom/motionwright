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

## Recent-first event journal acceptance

The storage tests create a 260-change SQLite journal and verify descending keyset pages, complete non-overlapping coverage, cross-project isolation, and backward compatibility with ascending event reads. Studio unit tests reject malformed/out-of-order pages; Chromium tests exercise recent-first pagination, older-page navigation and transient read retry through a synthetic Tauri boundary. All tests are source-SHA scoped and do not establish independent creative acceptance. See [history pagination](history-pagination.md).

## Source-verified rendered frame readback

The native-preview Rust unit tests verify owner-root manifest SHA-256, registered single-frame PNG bytes, stale revision rejection, size/path validation and tamper rejection. The browser regression deliberately uses synthetic Tauri frame bytes, testing only UI mode, seek and error handling. The real Native Motion Canvas E2E lane independently rehashes every rendered PNG against the actual Semwright artifact manifest. See [native frame preview](native-frame-preview.md). These checks are not real-time AV playback or human visual acceptance.

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

The same workflow has a separate `waveform-lazy` job for long-audio evidence. It builds the
benchmark first, then measures only the resulting runtime process while it imports a real one-hour
8 kHz PCM source, generates the cache-bound proxy and requests a distant page. CI requires the
source to exceed 50 MB, the proxy to stay below 64 KiB, a returned page to stay at 256 peaks or
less, and maximum resident memory to remain below 192 MiB. The second page must come from the
cache and complete inside the declared two-second CI bound. These figures prove the current
fixture and algorithm; they are not a universal desktop performance claim.

The `responsiveness-service` job builds a reference S editing profile through the real service
boundary: 12 scenes, 80 authored beats, 300 canvas nodes, a measured five-minute PCM voice source
and the default 1920x1080/30 deliverable. After warm-up it records 40 small SQLite-backed project
commits and enforces p95 <250 ms, then reopens the same database under the declared S metadata
budget of two seconds. The Tauri `apply_change` command dispatches its project load and mutation
through `spawn_blocking`, keeping those SQLite operations off the async invoke executor.

The independent `ui-selection` job seeds 12 scenes in Chromium and measures 60 in-browser
project-rail selections from the click dispatch through the Inspector update and next animation
frame. It enforces p95 <100 ms. The measurement is performed inside the browser so Playwright
transport latency is not counted as application latency. Exact JSON measurements and failure
artifacts are retained per SHA; these runner-specific budgets are not universal device claims.

Human feedback remains NOT_RUN until actual participants exist. The benchmark never manufactures
comparative claims against other editors. See `docs/derived-cache.md` for cache identity,
invalidation and recovery rules and `docs/audio.md` for waveform semantics.


## Variants and localization evidence lane

The Variants and Localization workflow is the exact-source regression lane for delivery derivation. It runs domain invariants for crop approval, locale voice/timing separation, protected narrative cuts and dependency invalidation; canonical Film projection for portrait/localized/cut output; Native SDK contract tests; Studio unit/type/build; and the focused Chromium delivery-variant acceptance.

That lane is deliberately separate from product-level human acceptance. A green workflow proves the implemented contracts for that SHA; it does not manufacture creative approval for an actual campaign or localized voice.
