# Motionwright

**Motionwright** is an open-source, Semwright-native creative production studio for maintaining audiovisual projects with human editing and AI-assisted direction.

It is not a prompt-to-video wrapper. A Motionwright project keeps brief, narrative, storyboard, canvas objects, timeline, assets, alternatives, locks, revisions, branches, reviews, renderer realizations and deliverables connected as one versioned project.

> Status: active development. The repository is intentionally explicit about what is implemented versus specified or still awaiting native acceptance.

## Why Motionwright

Most AI video workflows optimize for the first render. Motionwright is designed for revision ten: the moment when a line changes, a scene is moved, a renderer is swapped, a vertical cut is requested, and the project still needs to explain what changed, what became stale, which artifact came from which revision, and which operation is safe to retry.

The application owns its creative domain and SQLite transaction boundary. Semwright remains the authority for its Native SDK contract, Driver Host/Broker/Policy boundaries, Project Graph, Effect Conformance, jobs, sealed runtime tools and native render drivers.

## Product surface

The desktop editor is organized around:

- **Brief & Narrative** — audience, objective, claims, beats and scoped constraints.
- **Audio** — byte-bound measured voice takes, active-take selection, rational transcript alignment, stable cues and explicit mix intent.
- **Storyboard** — persistent scene cards with objective, duration, renderer, state and continuity consequences.
- **Canvas** — stable object identity, transforms, typed rational-time keyframes, hierarchy, relationships, safe areas, cameras and locks.
- **Timeline** — voice/music, transcript, beats, scenes, cues, objects, cameras, markers and review regions on one clock.
- **Jobs** — durable production receipts with execution state separated from CURRENT / STALE applicability.
- **Workflows** — explicit Semwright recording, trace compilation, advisory patterns/suggestions, exact proposal acceptance, candidate verification, replay and promotion through canonical Broker/Policy gates; no background recording or parallel workflow store.
- **Alternatives** — synchronized A/B/C comparison and explicit selection/merge into reviewable changes.
- **Changes** — revision history, branches, semantic diffs, conflict-aware merges and restore-as-new-change.
- **Dependencies** — canonical Project Graph projections and honest CURRENT / STALE / UNKNOWN status.
- **Review** — technical findings separated from creative critique and comments anchored to frame/object/beat/revision.
- **Deliver** — editable 16:9, 9:16, 1:1 or custom profiles, language/cut/brand intent, codecs/audio profiles, explicit native Motion Canvas segment production, fail-closed WebVTT/SRT sidecars, and portable project exports.

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
- Native production uses a digest-pinned owner connection to the Semwright CLI/Broker, strict Driver provenance, revision-bound receipts and fail-closed mutation retry semantics.
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

## Licensing

Motionwright is licensed under **GNU AGPL v3.0 or later**. Third-party components keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Semwright itself is a separate project and is not relicensed by this repository.

## Security / truthfulness

Do not commit secrets, private prompts, paid-provider credentials, proprietary fonts, customer media or private coordination kits. CI receipts and product status must stay attached to exact source revisions. A written test is not a passing test; a sampled frame is not proof of the whole video; a generated recommendation is not an Effect Conformance verdict.

The concrete WebView, controlled-import, portable-bundle and exact-source delivery boundaries are documented in [docs/security.md](docs/security.md). CI enforces the public source boundary and produces a delivery manifest tied to the tested Motionwright SHA and pinned Semwright revision.

## Acceptance status

Implementation status and product acceptance are intentionally separate. The public ledger in `docs/acceptance/` contains all 208 requirement IDs and 60 acceptance IDs without publishing the private specification text. CI rejects missing IDs and refuses product `PASS` without evidence plus independent review.
