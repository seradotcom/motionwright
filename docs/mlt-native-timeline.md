# Experimental pinned MLT multisegment timeline assembly

The existing Motionwright Native SDK owns a versioned creative film, real Motion Canvas segments, native video-only FFV1 intermediates, and a Semwright Broker/Driver Host connection. The new internal `ProductionCoordinator::assemble_native_mlt_video_timeline` constructs an **actual native semantic MLT video timeline** using that same Broker instead of invoking shell commands or implementing another media scheduler. This is an **experimental owner-only production method**, not yet a release-ready desktop feature.

## Closed canonical driver steps

For the current Motionwright project/generation/revision, saved deliverable and original source-bound render, the method independently reruns the [multi-segment source preflight](multi-segment-preflight.md) and generates the [deterministic source/clip recipe](mlt-multi-segment-preparation.md). It calls the verified per-segment FFV1 encoder from the previous stage, and requires all returned source identities, starts, frames, hashes and output names to agree.

It then uses only the following typed, allowlisted commands from the exact pinned Semwright v1.0.0 MLT provider:

1. `project.create` with a bounded Rec.709, progressive, stereo, square-pixel, rational-FPS profile.
2. `sequence.create` and `track.create` (a single video track).
3. `asset.import` for every verified, output-root-only FFV1 Matroska; no arbitrary WebView path is accepted.
4. `sequence.list` and `track.list` readbacks after mutations, because **Semwright invalidates opaque references whenever the native project revision changes**.
5. `clip.insert` for each source with exact start frame, half-open interval `[0,frame_count)`, no ripple and no silent gaps. Each mutation must return exactly one created entity, an advanced provider revision and an applied semantic result.
6. `sequence.duration`, verifying precise total frames and native FPS; then `render.plan` with the pinned `lossless` output profile, requiring `runnable=true`, exact frames/current project revision and no missing services.
7. `render.start`, bounded `render.status` polling, and `render.result`; only actual `SUCCEEDED`, video-only FFV1 Matroska with matching native frame count, expected output path, SHA-256-verified bytes and owner root is accepted.
8. `project.close` to free bounded provider resources, after verifying the video artifact.

Every step calls the existing Motionwright production receipt layer, has its own stable request identifier and passes only semantic entity references issued by the current Semwright provider revision. A lost/unknown mutation is not silently resent with a fresh identity. Foreign output roots, wrong codecs, mixed renderers, altered creative source, displaced clip times or provider revisions fail closed.

## Output and remaining limits

This method returns **`MltVerifiedVideoTimeline`** with the actual owner-root FFV1 Matroska SHA-256, frame count, current creative version, source FFV1 receipts and explicit scope:

`actual-native-mlt-ffv1-video-only-no-audio-master`

**No H.264/AAC master is produced by this method.** A separately checked 48kHz stereo WAV must be attached using the pinned `av.mux` with the same total frame/fps verification before a final master may be delivered. Neither a source readback token nor a generic filesystem action is exposed to the WebView. Multi-renderer Blender/Manim contributions, portrait/square MLT render profiles, transitions and nonrepresentable authoring curves are still unsupported.

The shared MLT provider retains a bounded number of in-memory edit projects and output files are created with no-overwrite semantics. A failure during a multi-step assembly can leave a temporary provider project or partial owner output; retained receipts and native authority must be reconciled rather than blindly retrying an uncertain mutation.

## Native render failure triage

The real two-segment CI uncovered a separate upstream Motion Canvas `render.status = failed` after correcting the original three-profile fixture. The safe renderer receipt identified `observation_count_incomplete` at 1 fps, prior to MLT. The updated fixture uses the 30 fps renderer baseline, 33 exact one-frame scenes, and checks all rational starts/durations after reopening SQLite. The pinned provider classified the first segment as `observation`, so the issue is upstream of semantic MLT assembly. To narrow that phase safely, the CI-only harness parses at most four small, regular, exact-name native failure receipts beneath the disposable owner output root, retaining only allowlisted observation subreasons in `safe-observation-failure.json`. It never publishes the receipt's local stack, arbitrary message, source identity or filesystem paths. Missing, malformed or unsafe receipts remain `unclassified`. Motionwright now retains only the **finite, typed Semwright `failure_class`** and the 1-based segment number in its error, never the raw driver `error` field, temporary owner paths, source media names or private JSON receipts. An unrecognized or missing class is reported as `unclassified`, not guessed. That bounded evidence distinguishes a rendering problem from downstream MLT semantic editing and preserves the requirement for a complete native E2E. The source-bound FFV1 video and an H.264/AAC master remain **unverified** until the actual test passes.

## Verification status

Rust unit/contract tests validate bounded native project references, nonadvancing or stale provider revisions, unique created entity refs, required timeline entity lookup cardinality, and fail-closed metadata. The source plan is covered by separate bounded recipe tests and the existing Semwright single-segment `frames.encode`/AV E2E lanes remain active.

The new **exact-SHA `native-mlt-sequence-e2e` CI lane** seeds 33 real one-frame scenes at 30 fps (each has exact rational duration 1/30 second), renders the canonical 32+1 Motion Canvas partitions through the pinned Broker/Driver Host, assembles them via Semwright's semantic MLT edit API, and checks the resulting 33-frame video-only FFV1 Matroska and every artifact SHA-256. This test must actually PASS for the PR's current SHA before the owner-only method may be advertised as a supported user-facing assembly capability or considered eligible for full product acceptance. Tests of a synthetic provider response are not evidence of an actual assembled video; no product acceptance scenario is promoted automatically.
