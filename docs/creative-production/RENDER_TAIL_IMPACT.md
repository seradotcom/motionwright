# MW05-E03-03 · Source-timeline render dependency windows

Status: **partial**, no actual cache invalidation or native-frame reuse accepted.

## Editable-source contract

Production → Render impact is a read-only inspector of a versioned project.
It distinguishes four explicitly scoped events:

1. scene_visual: the source timeline frames occupied by the selected scene.
2. scene_audio_tail: the scene and **owner-declared**, bounded 1–300 trailing
   source frames. Requires an identified, content-addressed audio Asset.
   The duration has NOT been independently measured from the waveform.
3. time_ripple: all selected deliverable source intervals at and after
   the earliest changed scene. It intentionally over-invalidates uncertain
   timing dependencies.
4. global_mix: the complete original source interval for all-scene
   profiles; only selected original scene intervals for scoped deliverables.

The inspector never mutates the project, its scenes, the journal, cache files,
audio tracks, provider processes or review state. It requires the **exact**
project generation/revision, rejects duplicated/unknown scene IDs, disallows
audio tail fields in non-audio requests and fails if a declared audio asset is
absent or lacks a digest.

## Timing and source mapping

Both Rust and the independent TypeScript implementation use exact integer
rational arithmetic for all frame boundaries. A scene starts at floor(start
* deliverable FPS) and ends at ceil((start + duration) * FPS), represented as
half-open [start,end) intervals on the original source timeline. Windows
overlapping or touching are deterministically merged. Every deliverable is
evaluated at its own exact FPS, including rational rates such as 30000/1001.

The result records impacted scene IDs and windows for each deliverable.
A profile selecting only some scenes does not silently treat its source
timeline coordinates as edited output-cut coordinates. Those coordinates
require a separate native edit-decision-list mapping and source-bound
render comparison. If an audio tail extends beyond the final source scene,
that condition is flagged; it does not silently expand the delivered master.

The analyzer is deliberately bounded: 256 source scenes, 64 deliverables,
1,080,000 source frames/profile, 1–120 FPS and audio tails of at most
300 frames. Unreasonable or overflowing rational input fails closed.

## Evidence limitations

The typed report explicitly fixes all three claims to unproven:
actually_invalidated_caches=false, native_frames_regenerated_or_avoided=null,
and native_renderer_saved_wallclock_micros=null. Its acceptance string is
DEPENDENCY_PREFLIGHT_ONLY_NATIVE_COMPARISON_NOT_RUN.

It is NOT a renderer cache, sound waveform analyzer, verified transition
graph, audio mixing engine, or measured frame-regeneration optimization.
A profile's generated frames must be compared with a full native render,
including transition/audio tails, before cached film segments can be safely
reused. A green test only establishes a conservative dependency preflight.

## Tests

Rust and independent Studio TypeScript regression suites cover one-scene
edits, audio tails crossing scene boundaries, scoped deliverables, ripple
timing, global mixes, exact 24 and 30000/1001 frame conversion, stale
generation/revision rejection, duplicate and absent sources, bounded frames
and the explicit non-performance claims. Playwright tests confirm the
read-only inspector and its refusal to invent audio tail evidence.

No approval, user time saving, sound waveform truth or release readiness is
inferred by these tests. The rest of MW05-E03-03, including real transition
dependencies and independently decoded incremental/full-render equivalence,
remains NOT_COMPLETED.
