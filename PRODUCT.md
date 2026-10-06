# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated by the user: Rust domain/service/storage with a React + TypeScript editor inside a Tauri 2 desktop shell. Motionwright consumes Semwright Native SDK from an exact public Git revision; the application owns its model and SQLite transaction boundary. Heavy build/render/browser/native checks run in CI.

## Users

Primary users are software/product teams, motion designers, technical storytellers, and agent-assisted creative operators who need to produce and maintain audiovisual projects with both human editing and AI assistance.

They work inside a long-lived project: import a brief, voice and assets; shape narrative; compare alternatives; edit storyboard/canvas/timeline; render through multiple backends; review evidence and creative feedback; branch and merge revisions; and export variants.

## Product Purpose

Motionwright is a Semwright-native creative production studio for directing, editing, rendering, reviewing, and maintaining audiovisual projects. The goal is not prompt-to-video automation. Human and agent edits operate on the same versioned project state, with explicit scope, locks, revision checks, renderer capabilities, recoverable jobs, review state, and portable deliverables.

Success means a user can complete the workflow locally without a commercial account: create/open a project, import assets, author narrative, edit visually, invoke Semwright-backed production, review results, recover from failures, branch/merge, produce variants, and export/reimport without hidden state.

## Positioning

Motionwright treats creative production as a versioned semantic project rather than a pile of generated clips or code. Intent, storyboard, timeline, canvas objects, assets, alternatives, revisions, Graph/Effects provenance, renderer realizations, and reviews remain connected after repeated edits. Generated code or media is a realization of approved intent, not the sole source of truth.

## Operating Context

- Local-first desktop application with explicit project files/state.
- Dark and light editor modes.
- Central program preview, project/navigation rail, contextual inspector, and bottom timeline.
- Shared playhead across voice, transcript/cues, storyboard, canvas, and review anchors.
- Scoped natural-language direction where the user sees and constrains the resources an agent may change.
- Motion Canvas + MLT is the first required production route; Blender and Manim Community are committed additional realizations.
- Semwright Native SDK/Driver Host/Broker/Policy/Project Graph/Effect Conformance/jobs remain the common authority where applicable.
- GitHub Actions is the final gate authority; the local workstation is reserved for source editing and lightweight checks.

## Capabilities and Constraints

- App-owned domain, persistence, transactions, branches, locks, revisions, and request receipts.
- Optimistic CAS on every target-bound edit; preflight observation alone is not atomic CAS.
- Timeline supports voice/music, text, beats, scenes, cues, objects, camera, clips, markers, snapping, trim/split/slip/ripple/reorder.
- Canvas supports stable object identity, selection, grouping, reparenting, ordering, alignment/distribution, transforms, text/style edits, relations, camera framing, safe areas, and locks.
- Storyboard preserves card identity when reordering and exposes continuity/dependency consequences.
- Alternatives are structurally distinct proposals with explicit selection/merge into reviewable changes.
- Technical verification and creative critique are separate channels.
- Jobs use Semwright runtime mechanisms rather than a private scheduler.
- Canonical workflow distillation remains owned by Semwright: Motionwright can explicitly record, compile, inspect, plan, accept, verify, replay and promote only through an allowlisted Broker/Policy boundary; no background recorder or private workflow authority exists.
- Variants include 16:9, 9:16, 1:1, captions/languages, cuts, codec/audio/brand profiles, and portable packages.
- No automatic paid-provider activation, publication, registry release, or hidden destructive cleanup.
- No arbitrary model-generated Python execution for renderers.
- No master prompts, private coordination files, or secrets are published in the public repo.

## Brand Commitments

Name: Motionwright.

Motionwright belongs to the Semwright product family and should feel like a serious creative workstation, not a generic SaaS dashboard. The editor must prioritize the work itself over decorative metrics. Brand language is direct, technical when useful, and honest about CURRENT / STALE / UNKNOWN states.

## Evidence on Hand

- Semwright source repository at `seradotcom/semwright`.
- Verified integration baseline for this build: `4d291de26724810017ce7b6d185326514cb79fa6` on `origin/main` when Motionwright was initialized.
- Public Native SDK docs and Rust crate are available in that snapshot.
- A detailed private specification pack supplied by the user defines 208 requirements and 60 acceptance scenarios; the private coordination material itself is not copied into the public repository.
- External UI references reviewed include DaVinci Resolve, Adobe Premiere Pro, Final Cut Pro, Descript, Runway, and Impeccable design guidance.
- No production voice, customer data, paid-provider credentials, or proprietary font files are assumed present in this repository.

## Product Principles

1. One project state: human UI and agents edit the same versioned domain through explicit transactions.
2. Intent before realization: media/code outputs derive from maintained semantic intent and can be rebuilt or swapped with declared loss.
3. Truthful evidence: technical PASS, creative advice, renderer support, job state, and staleness are distinct and never cosmetically merged.
4. Local-first, Semwright-connected: Motionwright owns creative state; Semwright owns shared authority, runtime, drivers, Graph/Effects, and policy boundaries.
5. Revision ten matters: the product is judged by maintainability after repeated revisions, not by the first impressive render.

## Accessibility & Inclusion

Keyboard navigation, visible focus, textual state semantics, reduced motion, UI zoom/reflow, useful empty/loading/error/offline states, and WCAG-oriented contrast are product requirements. Color is never the only carrier of CURRENT / STALE / UNKNOWN or validation status.
