# Motionwright

**Motionwright** is an open-source, Semwright-native creative production studio for maintaining audiovisual projects with human editing and AI-assisted direction.

It is not a prompt-to-video wrapper. A Motionwright project keeps brief, narrative, storyboard, canvas objects, timeline, assets, alternatives, locks, revisions, branches, reviews, renderer realizations and deliverables connected as one versioned project.

> Status: active development. The repository is intentionally explicit about what is implemented versus specified or still awaiting native acceptance.

## Why Motionwright

Most AI video workflows optimize for the first render. Motionwright is designed for revision ten: the moment when a line changes, a scene is moved, a renderer is swapped, a vertical cut is requested, and the project still needs to explain what changed, what became stale, which artifact came from which revision, and which operation is safe to retry.

The application owns its creative domain and SQLite transaction boundary. Semwright remains the authority for its Native SDK contract, Driver Host/Broker/Policy boundaries, Project Graph, Effect Conformance, jobs, sealed runtime tools and native render drivers.

## First use on desktop

Use a candidate supplied for testing and compare its SHA-256 with the package receipt for the same commit before opening it. The current CI candidates are unsigned, and the macOS DMG is unnotarized. If the operating system blocks the package, use an approved distribution when it becomes available. [Package checks and supported artifacts](docs/installed-candidate-smoke.md)

| Platform | Candidate |
|---|---|
| Linux x86-64 | Run the AppImage with your normal user account. The Debian package has a separate install path. |
| Windows x64 | Use the NSIS setup program for your current account. |
| macOS Apple Silicon | Open the DMG and copy the app to a location your account can write to, such as your user Applications folder. |

1. Open the project library and choose **Create project** or **Create editable motion example**. Save a change, close Motionwright, then reopen the project from the library to resume editing.
2. To move the saved project and its original assets, use **Deliver**, choose a new folder with **Choose portable bundle destination**, then select **Export bundle**. On the receiving installation, open **Import a portable project**, choose the folder, and use **Inspect portable project** before selecting **Import inspected project**. Import creates a new execution generation and refuses to overwrite an existing project. [Portable backup behavior](docs/reliability/bundle-export-destination-authority.md)
3. When you are ready to render, open **Integrations** to select the Semwright connection for this project and check runtime compatibility. Local editing and backup work before you configure a renderer. [Runtime setup](docs/owner-native-runtime-onboarding.md)

## Product surface

The desktop editor is organized around:

- **Brief & Narrative** — audience, objective, claims, beats and scoped constraints.
- **Audio** — byte-bound measured voice takes, active-take selection, rational transcript alignment, stable cues and explicit mix intent.
- **Storyboard** — persistent scene cards with objective, duration, renderer, state and continuity consequences.
- **Canvas** — stable object identity, transforms, typed rational-time keyframes, optional [atomic Auto-key X/Y](docs/motion-authoring.md) and [frame-based native linear move authoring](docs/native-linear-position-motion.md) for position drags, hierarchy, relationships, safe areas, [live semantic camera projection](docs/canvas-camera.md) and locks.
- **Timeline** — voice/music, transcript, beats, scenes, cues, objects, cameras, markers and review regions on one clock. Canvas keyframes and Audio cues share the same playhead; frame stepping follows the selected profile; users can switch between semantic intent, source-verified native PNG frames and an exact-scoped [native H.264/AAC AV monitor](docs/program-av-monitor.md) when the completed master matches the current scene, profile and revision (see [editor clock](docs/editorial-clock.md), [timebase](docs/editor-timebase.md), [native frame readback](docs/native-frame-preview.md) and [receipt projection](docs/render-receipt-program.md)).
- **Jobs** — durable production receipts with execution state separated from CURRENT / STALE applicability, plus [receipt-watermark-bound Native SDK pagination](docs/production-receipt-pagination.md) and [user-initiated historical browsing](docs/production-jobs-history.md), independent of creative revisions.
- **Workflows** — explicit Semwright recording, trace compilation, advisory patterns/suggestions, exact proposal acceptance, candidate verification, replay and promotion through canonical Broker/Policy gates; no background recording or parallel workflow store.
- **Alternatives** — synchronized A/B/C comparison and explicit selection/merge into reviewable changes.
- **Changes** — recent-first paged committed event history, branches, semantic diffs, conflict-aware merges and restore-as-new-change (see [journal pagination](docs/history-pagination.md) and [Native SDK array pagination](docs/native-scope-pagination.md)).
- **Dependencies** — honest, recorded local VoiceTrack→asset references and explicit [SHA-256 asset integrity inspection](docs/asset-integrity.md); canonical Project Graph CURRENT / STALE / UNKNOWN authority remains upstream, never inferred from asset list position.
- **Review** — technical findings separated from creative critique and comments anchored to frame/object/beat/revision.
- **Deliver** — editable 16:9, 9:16, 1:1 or custom profiles, language/cut/brand intent, codecs/audio profiles, [read-only canonical Film preflight](docs/film-preflight.md), [static native camera pan](docs/native-film-static-pan.md), [native linear position motion](docs/native-linear-position-motion.md), explicit native Motion Canvas segment production, [read-only multi-segment source preflight](docs/multi-segment-preflight.md), [deterministic MLT edit/FFV1 source preparation](docs/mlt-multi-segment-preparation.md), [source-bound multisegment audio readiness](docs/mlt-multisegment-audio-readiness.md), an [experimental native FFV1/PCM semantic MLT intermediary assembler](docs/mlt-native-timeline.md) (not a final master), and [single-segment H.264/AAC mastering](docs/desktop-av-master.md) with a [decoded-content CI gate](docs/native-av-decoded-content.md), from measured voice with [bounded real MP4/AAC playback](docs/native-av-review.md) and [verified local MP4 delivery](docs/master-verified-export.md) with optional [portable SHA-256 receipt](docs/portable-mp4-integrity.md) and [read-only in-app local verification](docs/local-mp4-integrity-review.md), fail-closed WebVTT/SRT sidecars, and portable project exports.

## Product acceptance and independent evidence

The public repository tracks 208 requirement IDs and 60 independent product acceptance IDs without publishing the private specification. Automated implementation tests do **not** promote acceptance cases to PASS. The [private review intake](docs/acceptance/REVIEW_INTAKE.md) creates source-SHA-bound, separately reviewed evidence records outside the public checkout, validates actual file SHA-256 and never self-certifies a result. Current independent acceptance remains `NOT_RUN` until executed and reviewed. The [source-bound v0.1.0 release-readiness report](docs/v01-release-readiness.md) assembles open implementation, security, signing and external evaluation gates without converting successful CI into publication authority.

## Native SDK

Motionwright pins Semwright exactly in [SOURCE_LOCK.json](SOURCE_LOCK.json). The Rust integration consumes the public `semwright-native-sdk` crate from that Git revision rather than depending on a private worktree path.

The Native SDK surface is used as intended:

- Motionwright keeps its own model and database.
- observations return opaque application revisions;
- target-bound mutations compare the expected revision inside the same SQLite transaction as the commit;
- operation discovery is not permission;
- the application cannot mint Broker approvals, grants or Driver Host authority;
- Graph and Effects are adapters to canonical Semwright authorities, not local replicas.
- Graph candidates and Effects specifications are prepared through the pinned Native SDK; admission/verdict authority remains outside the application.
- Native production uses a digest-pinned owner connection to the Semwright CLI/Broker, strict Driver provenance, revision-bound receipts and fail-closed mutation retry semantics. Installed desktop users can [select an owner-provisioned connection using a trusted OS picker](docs/owner-native-runtime-onboarding.md) in Integrations; the WebView never receives its contents or path, and runtime installation/permission is not automatic.
- Motion Canvas production projects only explicit Motion Canvas timeline segments into the pinned canonical Film schema; Studio supplies explicit narrative-role/archetype intent, then the production coordinator executes Semwright composition plan/apply, bounded render polling and native verification before exposing artifact evidence. Mixed-renderer scenes remain in their native lanes.

## Renderer plan

The required production path is **Motion Canvas → MLT/audio/master** through Semwright capabilities. Blender provides bounded geometry/material contributions through the pinned Semwright native driver. Manim Community is a separate bounded semantic realization implemented as a Motionwright-owned Semwright Application Driver: typed plans only, Driver Host jobs, a sealed runner with pinned Python/FFmpeg dependencies, no render-time network authority, and digest-verified MP4 output. Remotion and ManimGL may be supported as optional profiles but are not required for the free base path.

No renderer integration is allowed to execute arbitrary model-authored Python.

## Repository layout

```text
crates/
  domain/       creative project model, time, revisions, changes and validation
  storage/      SQLite persistence, CAS, receipts, event journal and recovery
  service/      application use-cases shared by UI and Native SDK adapter
  manim-profile/ shared bounded Manim scene/profile contract and fixed-vocabulary compiler
  driver-manim-community/ Semwright Driver SDK provider and sealed runtime runner
  native/       public Semwright Native SDK cooperation adapter
apps/
  studio/       React/TypeScript editor
  desktop/      Tauri 2 shell
docs/
  architecture.md
  native-sdk.md
  motion-authoring.md
  renderers.md
  security.md
  workflows.md
  testing.md
  visual-references.md
```

## Development policy

The workstation is intentionally kept light. Source-format checks and small unit checks may run locally. Cargo builds/tests/clippy/docs, browser suites, Tauri bundling, native driver integration, rendering, coverage, fuzzing and large media fixtures run in GitHub Actions.

Candidate packaging is also CI-only. Linux AppImage/Debian, current-user Windows NSIS and macOS DMG artifacts are hashed into exact-source receipts. The [isolated installed-package smoke gates](docs/installed-candidate-smoke.md) additionally inspect extracted/deployed binaries, require two separate rootless Linux AppImage windows and check the same fresh SQLite project survives closing/reopening without mutation, and exercise a silent Windows current-user installation on disposable runners. No result implies signing, notarization, human install approval or automatic release publication.

## Licensing

Motionwright is licensed under **GNU AGPL v3.0 or later**. Third-party components keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Semwright itself is a separate project and is not relicensed by this repository.

## Security / truthfulness

Do not commit secrets, private prompts, paid-provider credentials, proprietary fonts, customer media or private coordination kits. CI receipts and product status must stay attached to exact source revisions. A written test is not a passing test; a sampled frame is not proof of the whole video; a generated recommendation is not an Effect Conformance verdict.

The concrete WebView, controlled-import, portable-bundle and exact-source delivery boundaries are documented in [docs/security.md](docs/security.md). CI enforces the public source boundary and produces a delivery manifest tied to the tested Motionwright SHA and pinned Semwright revision.

## Acceptance status

Implementation status and product acceptance are intentionally separate. The public ledger in `docs/acceptance/` contains all 208 requirement IDs and 60 acceptance IDs without publishing the private specification text. CI rejects missing IDs and refuses product `PASS` without evidence plus independent review.

## Creative production expansion

The v0.5 creative-production branch adds an implemented, bounded workstation slice; it is not a completed v0.5 release. See [creative production](docs/creative-production/README.md) for editable components, native inspection, scoped undo, portable design samples, document-format migration and explicit remaining work. [The expansion ledger](docs/creative-production/requirements-status.json) preserves all 64 expansion IDs separately from the original acceptance history.
