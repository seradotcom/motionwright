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

## Automated desktop candidate install/extraction inspection

The Candidate Packages workflow verifies exact bundle SHA-256 against its source receipt, extracts Linux Debian/AppImage bundles, inspects real installed PE/Mach-O/ELF executable identity and desktop/bundle metadata, performs a disposable NSIS current-user installation and checks a rootless Xvfb AppImage window. Its 8 Python adversarial unit tests refuse altered archives, mismatched Semwright/source stamps, architecture/metadata substitution and fabricated GUI evidence. These checks are explicitly [automated package smoke](installed-candidate-smoke.md), **not human-installed platform acceptance**, media compatibility, notarization or publication.

## Native SDK project-scope enumeration

Source-bound Rust tests enumerate 271 project-owned records with several bounded Native SDK page limits, verifying complete, ordered, nonoverlapping coverage and reject invalid offsets, wrong-scope cursors and stale creative revisions. Integration writes eleven real scenes and checks both Timeline and Canvas native observation pagination end-to-end through StudioService. Production Jobs use a separate [append-only receipt watermark and keyset history](production-receipt-pagination.md), rather than a project-revision-only offset cursor; the latest-window Studio UI stays lightweight, while a separately requested [Full history](production-jobs-history.md) view walks bounded job pages with explicit stale-receipt recovery. The browser regression uses synthetic Tauri transport to check 42 jobs over three pages and does not infer Driver Host state. See [Native SDK scope pagination](native-scope-pagination.md).

## Recent-first event journal acceptance

The storage tests create a 260-change SQLite journal and verify descending keyset pages, complete non-overlapping coverage, cross-project isolation, and backward compatibility with ascending event reads. Studio unit tests reject malformed/out-of-order pages; Chromium tests exercise recent-first pagination, older-page navigation and transient read retry through a synthetic Tauri boundary. All tests are source-SHA scoped and do not establish independent creative acceptance. See [history pagination](history-pagination.md).

## Semantic camera projection and world-space pointer editing

Canvas applies the stored scene camera center, zoom and inverse rotation to its editorial object layer, while retaining a viewport-fixed safe-frame overlay. Pure TS tests check math for identity and panned/rotated/zoomed cameras and inverse pointer movement. Chromium tests exercise camera controls and drag gestures under 2× zoom/90° camera rotation without inadvertently creating motion keyframes. This is semantic editing quality, **not rendered-frame parity or human acceptance**. See [camera projection](canvas-camera.md).

## Atomic Canvas Auto-key motion

Rust domain tests verify one authored revision for an X/Y position pair, no base-transform mutation, strict property locks, rational half-open scene timing, and rejection of invalid coordinates without half-keyframe commits. Native SDK public-operation tests validate the closed contract for agent use. Browser fixture and Chromium pointer regressions compare Auto-key on/off, exact scene-local timing, history event count, and locked-gesture no-op. Native renderer Film still rejects motion it cannot preserve exactly; see [typed motion authoring](motion-authoring.md).

## Atomic frame-based native position authoring

The Canvas inspector can create exactly four source-bound linear position keys with one revisioned Change, as opposed to manually inserting two pairs in separate commits. Rust domain and service tests cover frame-rate rational conversion, source base preservation, locking and no-overwrite semantics. Native SDK closed-schema tests verify the capability for agents, and browser tests verify explicit gating and serialized source identity. Native render/pixel support remains an independent gate.

## Native Film exact linear X/Y position motion

The pinned Film adapter admits only paired, exact-frame-aligned linear X/Y motion from an authored pose at scene-local 0 to the unchanged node base at T. Rust tests verify the actual native Settle and compiled Position tween, unsupported easing/channels/missing axes, frame alignment and starting-frame safe areas. The real pinned Broker/Driver Host E2E now seeds a visible keyed tile and independently checks 60 native frame hashes, including distinct frame 0 and frame 30 PNGs. This is native runtime evidence, **not independently approved creative quality**. See [motion projection](native-linear-position-motion.md).

## Native Film static camera pan

The pinned Film adapter now projects per-scene static camera center shifts through native subject translation when zoom=1 and rotation=0, keeping source Canvas geometry, text size, frame count and safe areas explicit. Rust tests exercise two panned scenes, native Film realization, unsupported zoom/rotation and unsafe pan bounds. This is not independent rendered pixel parity. See [static camera pan](native-film-static-pan.md).

## Bounded native MLT FFV1 source preparation

A deterministic, owner-only MLT timeline recipe maps each preflight segment to an exact half-open clip interval, stable SHA-256-derived FFV1 output name and pinned landscape profile. Rust tests verify source/scene/job uniqueness, gap-free frame accounting and no generated media claims. The internal production coordinator revalidates the native source preflight before invoking the pinned Semwright MLT frames encoder per segment, verifies video-only FFV1 codec/size/FPS and checks actual artifact bytes. These are separate verified intermediates, **not** a completed multisegment MP4; see [MLT preparation](mlt-multi-segment-preparation.md).

## Multi-segment native MLT source conformance

Rust tests compile 33 authored Motion Canvas scenes into canonical 32+1 native Film segments and check an exact, manifest-SHA-verified 990-frame MLT assembly preflight. They reject stale project revisions, duplicated job references, segment reordering, altered native verdicts, tampered manifest bytes, missing source segments, mixed-renderer cuts and unsupported portrait-only MLT output. **This is read-only source preparation, not a rendered multi-segment master.** See [multi-segment MLT source preflight](multi-segment-preflight.md).

## Native Film semantic preflight

The native projection preflight uses the exact pinned Semwright Film adapter and current Motionwright project revision, with no native render dispatch or effect grant. Chromium tests exercise the typed read-only contract, supported/unsupported status and invalidation when creative inputs change. Native Film support is a semantic planning result, never certified renderer pixels or human acceptance; see [preflight semantics](film-preflight.md).

## Exact-scoped Program AV monitor

When both a verified Motion Canvas segment and the corresponding MLT H.264/AAC master are present for the selected versioned scene/profile, the Program monitor maps the output cut clock onto the project-global editorial playhead without altering project state. The pure-source tests cover fractional FPS, included/omitted scenes, mismatch rejection and reverse seek to authored time. Synthetic Chromium tests verify no master playback is fabricated for wrong profile, renderer or revision. See [Program native monitor](program-av-monitor.md). Installed-WebView decoder behavior and exact A/V sync still require separate acceptance.

## Bounded in-app native AV review

The desktop may read a previously authenticated Semwright H.264/AAC MP4 into WebView media memory **only** on explicit user request, using the session export token and a source/project revision match. Rust tests verify exact SHA-256 source bytes, invalid ftyp, stale project, source tampering and 16 MiB cap; synthetic Chromium tests confirm read-only token-only IPC and opt-in UI flow, but do not claim actual decoder compatibility. See [native AV review](native-av-review.md). Larger masters remain available through streaming verified MP4 export.

## Verified local native MP4 delivery

The desktop native MP4 delivery boundary accepts only a minted session grant for an already-completed, source-bound MLT master. Native Rust tests verify scoped source identity, refused symlinked export parents, create-new/no-overwrite, bounded SHA-256-verified streaming, and refusal to copy changed master bytes. Synthetic Chromium tests cover explicit user destination, one-time DeliverLocal grant, request parameters and revision invalidation. See [verified master export](master-verified-export.md); green tests do not certify signed distribution or human review.

## Canonical desktop AV mastering

The desktop MLT AV workflow passes only an authenticated native frame-session token and an already-saved, measured 48 kHz stereo WAV voice take ID. Rust tests validate source CAS digests, refuse stale/mono/non-WAV/unbound inputs, reject WAV length/rate/symlink/tamper, verify deterministic staging and bounded idempotent reuse of exact source SHA, refuse corrupted prior files, and check rational audio/video duration to one sample. The browser tests use an explicitly synthetic Tauri bridge to prove the UI never supplies arbitrary source paths or master evidence. Actual Semwright Broker/MLT H.264/AAC MP4 output remains covered by the exact-SHA [native AV master E2E lane](desktop-av-master.md) and requires independent product acceptance.

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
