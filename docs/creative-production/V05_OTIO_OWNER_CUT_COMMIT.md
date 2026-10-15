# Motionwright v0.5 — owner-reviewed OTIO cut commit

**Scope:** MW05-E11-03, bounded continuation of the existing
[`V05_OTIO_MEDIA_INTERCHANGE.md`](V05_OTIO_MEDIA_INTERCHANGE.md).
This feature extends the already verified `tooling/otio-media/bridge.py`
and the existing `Project` / `StudioService` / Semwright Native SDK paths.
It **does not** create a new file importer, external video renderer,
media relinker, effect executor, payment system or publication authority.

## What is now owner-committable

The verified Python bridge still performs the expensive **read-only**
file/audio/video/provenance checks first. It outputs the original
`motionwright.otio-edit-preview/1` JSON with the exact source
project ID, generation, revision and digest. The editor now has a
**Deliver → OTIO cut review** station where the user pastes that output,
sees the complete proposed order/end trims and the inherited fidelity/loss
report, and *separately* reviews and accepts the proposal.

The proposal is bounded to:
- A complete original 1–32 scene permutation, no added/deleted scene.
- A canonical minimal ordered set of `MoveScene` changes.
- At most one strictly shorter `SetSceneDuration` per scene, exact
  rational time and within 600 seconds.
- Exactly the original twelve loss/fidelity classifications.
- The original schema and exact project ID, generation, source revision;
  no forged `edit_imported`, `media_relinked`, render or publication claim.
- Explicit reviewer declaration, rationale, loss acknowledgement and
  acknowledgement that absolute voice/transcript/cues/markers do **not**
  move with the altered timeline.

The existing Studio `apply_change` requests the ordinary Tauri
`project_edit` effect grant. `Change::ApplyOtioOwnerCutReview`
reconstructs and validates the minimal source-bound native edit sequence
inside Rust; it executes the **already implemented** `MoveScene` and
`SetSceneDuration` mutations against a candidate copy. If any
late scene lock, strand of an authored beat/keyframe, project lock, scene
approval or unresolved review fails, the *entire* operation fails with
no partially applied move. Only the canonical StudioService SQLite CAS
can append a **single revision**, and Native SDK exposes that same change
as `driver.motionwright.otio-cut.owner-commit` with an observed-resource
CAS and durable request key. No alternate transaction manager exists.

The browser demo can inspect a source-bound proposal but cannot commit
it. UI revision, generation and project scope must remain unchanged from
inspection until confirmed submission; the CAS rechecks at commit.

## Important evidence limitations

This is a **manual acceptance of an existing preflight's exact subset of
edits**, not verification that arbitrary pasted JSON came from the
Python bridge or that any external NLE truly imported/rendered the cut.
The included `source_project_sha256` is retained as a declaration;
it is not a signature. The bridge's source-media SHA checks remain
separate and must be run against the real original files first.

No external media bytes, effects, clip in-points, audio mix, fonts,
captions, geometry, camera, locks, reviewer identity or publishing
rights are authenticated by this UI. The original application state
remains authoritative. Existing audio/marker timestamps keep their
absolute positions; users must reconform them manually as needed.
Human approval is **self-declared**, never an independent acceptance.

The old bridge had one narrow gap: it rejected source offset changes
but would formulate `SetSceneDuration` for a scene extension if
the source video contained spare frames. The bridge now rejects a
scene duration *greater than* the original project duration, so its
"end trim only" promise is exact.

## Verification and noncompletion

Source test: `tooling/otio-media/test_bridge.py`, including a
31-frame attempt to extend a 30-frame original scene.

Rust domain adversarial tests: `crates/domain/tests/otio_cut_owner_review.rs`
including forged identities, missing property loss, duplicate moves,
invalid trim extension, protected scenes, absolute markers, and
the late-failure atomic rollback.

Service tests: `crates/service/tests/otio_cut_commit_persistence.rs`,
including one saved revision for combined operations, durable replay,
stale rejection, SQLite reopen and portable reimport generation drift.

Native SDK: `crates/native/src/otio_cut_tests.rs`, including
observed-resource CAS and no mutation from application-local context.

Studio: `otioCutReview.test.ts` and `otio-owner-cut.spec.ts` use
explicitly synthetic preview data and test that the user cannot
commit until reviewing losses and existing absolute anchors.
They do not claim native video evidence.

**Not complete:** Kdenlive, Shotcut, Resolve and independent human
receiver acceptance are still NOT_RUN, as are authenticated rights,
full OTIO effect/transcript/camera fidelity and a complete external
NLE edit round-trip. All 60 independent creative acceptance sessions
remain unfinished. Release authorization remains false.
