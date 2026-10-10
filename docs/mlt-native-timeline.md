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
7. `render.start`, bounded `render.status` polling, and `render.result`; only actual `SUCCEEDED`, the pinned **FFV1 + PCM s16le** lossless Matroska profile with the exact owner-root output path, SHA-256-verified bytes, profile dimensions and source frame-clock-consistent duration is admitted. If Matroska reports no `nb_frames`, the result explicitly records `native_frame_count_observed=null`: the independent CI decoder must count all 33 frames before technical acceptance.
8. **No unattended `project.close` operation.** The pinned MLT provider declares that operation destructive and requires trusted foreground Broker consent. This application function neither requests it in a headless render nor fabricates approval. The response explicitly records `provider_project_cleanup=not_requested_requires_foreground_broker_consent`. The bounded in-memory project remains until an authorized foreground operator closes it or the provider process ends.

Every step calls the existing Motionwright production receipt layer, has its own stable request identifier and passes only semantic entity references issued by the current Semwright provider revision. A lost/unknown mutation is not silently resent with a fresh identity. Foreign output roots, wrong codecs, mixed renderers, altered creative source, displaced clip times or provider revisions fail closed.

## Optional pinned Semwright video-only native lossless intermediate

Semwright upstream PR [#254](https://github.com/seradotcom/semwright/pull/254)
is included through an intentional immutable Native SDK and Driver Host pin
`04142a2ae16e53a58bb6cf43135786e396a4b720`. This adds a second,
closed, source-bound output of this *same* semantic project editor:
`ProductionCoordinator::assemble_native_mlt_video_only_timeline`.
It creates a distinct no-overwrite output path and chooses the provider's
`lossless-video-only` FFV1 Matroska profile. The original
`assemble_native_mlt_video_timeline` remains FFV1+PCM and is preserved.
Neither mode accepts arbitrary render scripts, engine flags, or model/user
filesystem paths.

For video-only output, **the Host itself must decode and count every frame**:
the Semwright receipt must report the expected source frame count (not
`null`), exactly one FFV1 video stream, no audio, the intended owner-root
file, original project revision, bounded dimensions/duration and content
SHA-256. Any missing or false observation is rejected *before* Motionwright
accepts the new artifact. The source segments, semantic clip spans and
reference readbacks stay identical; there is no alternate local renderer.

The GitHub Actions E2E uses independent disposable `pcm` and
`video-only` lanes. Each rebuilds and runs both providers at exact SHA,
renders real 32+1 Motion Canvas frames, assembles MLT, verifies the source
and final file hashes, and decodes the result using an independent
`ffprobe -count_frames`. The video-only lane also rejects even a silent PCM
track. Passing this proves an editable lossless visual intermediate, **not**
a combined H.264/AAC master, approved sound, or a new product acceptance.
Downstream mux of measured audio remains separate and must use the native
`driver.mlt-video.av.mux` capability, with its own source-bound authority,
verification and end-to-end media checks.

## Native multisegment H.264/AAC with measured source WAV (experimental)

The additional trusted `assemble_native_mlt_multisegment_av_master` route
accepts only a **previously verified, revision-bound, video-only semantic MLT
intermediate** plus the exact WAV from Studio's own content-addressed library.
It re-preflights all 32+1 source clips, original source manifests, project
revision, deliverable and stereo/Rec.709 export contract. The WAV must be
bound to the selected deliverable and independently measured 48 kHz stereo,
with actual RIFF sample count matching the 33-frame rational cut to within
one sample. Unknown voice gain, loudness normalization or unimplemented
mixing is refused rather than silently dropped.

The caller must stage the verified CAS WAV into the trusted output root
using exclusive/create-new semantics (the existing desktop WAV stage helper
already performs this; no user paths reach the Driver). The coordinator
then sends the **two separately SHA-verified owner output files** to the
pinned `driver.mlt-video.av.mux` via the normal Native SDK/Broker/Driver Host,
and requires an actual H.264/AAC 48 kHz stereo MP4 with correct frame count,
geometry, output SHA-256 and a separately verified decoded-audio WAV
receipt. No second backend, local command invocation, arbitrary filter or
destructive MLT project cleanup is introduced.

The exact-SHA CI matrix adds a third disposable `final-mp4` mode. It
imports an actual 52,800-frame/48 kHz stereo test WAV through Studio,
with **independent 400 Hz left and 600 Hz right channel fundamentals**.
It binds the source to the saved 33-frame profile, renders and assembles
the same Semwright 32+1 semantic cut, and invokes the native AV mux.
An independent FFprobe counts all 33 decoded H.264 frames and verifies AAC
48 kHz stereo, while bounded CI-only FFmpeg decodes the **actual final MP4**
to s16le and verifies the known independent left/right signatures. Swapped,
duplicated, missing, silent or malformed PCM samples fail the same source
contract; negative tests exercise that gate without running a renderer.
All master/intermediate and audited decoded WAV hashes must still match.
AAC sample quantization may add up to one 1024-frame access unit; the
original imported WAV is checked to sample precision before authorizing mux.
This is **technical decoded media verification**, not production narration,
human mix approval or a creative-quality verdict.
The trusted desktop command and Studio UI entry must be separately reviewed
before this can be offered as a full user-facing editing workflow.

## Output and remaining limits

This method returns **`MltVerifiedLosslessTimeline`** with the actual owner-root FFV1 + PCM Matroska SHA-256, source frame count, nullable provider-observed frame count, current creative version, FFV1 source receipts and explicit scope:

`actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master`

**The pinned Semwright curated lossless MLT profile always outputs PCM transport audio**, even for a visual-only source timeline. Its presence does **not** mean the source contained voice/music, the audio was measured or approved, or the file is video-only. A missing Matroska `nb_frames` is not treated as a counted frame: the exact-SHA remote E2E independently decodes and counts every frame. **No H.264/AAC master is produced by this method.** A separately checked measured 48 kHz stereo WAV must still be explicitly source-bound and delivered with the final mux before a master may be offered. Neither a source readback token nor a generic filesystem action is exposed to the WebView. Multi-renderer Blender/Manim contributions, portrait/square MLT render profiles, transitions and nonrepresentable authoring curves are still unsupported.

The shared MLT provider retains a bounded number of in-memory edit projects and output files are created with no-overwrite semantics. **Even successful headless renders can retain an in-memory edit project:** cleanup is explicitly deferred, never falsely reported as complete. Closing through the Broker needs trusted foreground authorization, which is not requested or bypassed by this agent path. In disposable CI, the provider terminates when the runner tears down its own sandbox; this is not equivalent to a product-level cleanup receipt. A failure during multi-step assembly can also leave a temporary project or partial owner output; recorded receipts and native authority must be reconciled before retries.

## Proven native visual asset projection boundary

The first source-bound `final-mp4` real CI uncovered a new InvalidArgument
before the semantic MLT stage: adding an actual measured voice WAV to the
Motionwright Project/CAS caused the Film projection to include that
**unreferenced audio asset** in a Motion Canvas composition plan. Semwright
correctly rejects unimported assets in its managed visual registry, even
when the file has a valid SHA-256. Audio has its own separately verified
`av.mux` source contract; it does not belong in a source-bound visual Film.

Canonical Film now projects digest-bound assets **only for authored Image
or Video subject references**. The set is deduplicated, bounded to 128,
looks up exact Project asset identity and verifies its digest before
including it; unknown or unverified *referenced* assets fail closed.
Unreferenced WAVs, music and other unrelated CAS imports are not silently
submitted to the native Motion Canvas managed project. A Rust regression
compares the exact canonical Film timing/shot projection with and without
an independently imported, SHA-bound voice asset. This preserves both the
original video-only render and the legitimate separately measured audio
workflow rather than suppressing the native policy failure.

## Native render failure triage

The real two-segment CI uncovered a separate upstream Motion Canvas `render.status = failed` after correcting the original three-profile fixture. The safe renderer receipt identified `observation_count_incomplete` at 1 fps, prior to MLT. The updated fixture uses the 30 fps renderer baseline, 33 exact one-frame scenes, and checks all rational starts/durations after reopening SQLite. The pinned provider classified the first segment as `observation`, so the issue is upstream of semantic MLT assembly. To narrow that phase safely, the CI-only harness parses at most four small, regular, exact-name native failure receipts beneath the disposable owner output root, retaining only allowlisted observation subreasons in `safe-observation-failure.json`. It never publishes the receipt's local stack, arbitrary message, source identity or filesystem paths. Missing, malformed or unsafe receipts remain `unclassified`. Motionwright now retains only the **finite, typed Semwright `failure_class`** and the 1-based segment number in its error, never the raw driver `error` field, temporary owner paths, source media names or private JSON receipts. An unrecognized or missing class is reported as `unclassified`, not guessed. That bounded evidence distinguishes a rendering problem from downstream MLT semantic editing and preserves the requirement for a complete native E2E. The source-bound FFV1 video and an H.264/AAC master remain **unverified** until the actual test passes.

## Candidate fix for native scene-boundary frame loss

The pinned Semwright Native SDK's authoring runtime supports multiple exact rational-time **shots within one native Motion Canvas scene**, including per-shot root visibility from the original timeline. Earlier source-bound E2E diagnostics showed just 2 of 32 and 85 of 96 actual exported frames when each tiny editorial scene became an independent native Motion Canvas scene; changing the source fps did not repair scene transition loss (see [issue #81](https://github.com/seradotcom/motionwright/issues/81)). Motionwright now projects contiguous editorial scenes within a native segment as individually constrained, authored **beats/shots under one bounded run-level native Sequence**. The original scene IDs, cut order, shot IDs, source spans, full exact durations, and explicit source rendering receipts are preserved. The existing 32+1 segment boundary stays intact; this only avoids the per-editorial-scene native playback transition. Static, animated, and deliberately locked source objects still pass through the original Semwright authoring API; this is not rasterization, a direct FFmpeg path or a new backend. Unit tests prove all 33 exact one-frame scenes remain 32+1 source-bound shots with precise rational starts. A native runtime render is **NOT considered fixed** until the E2E exports all 32+1 expected frames and independently verifies the final MLT video and hashes. No H.264/AAC finished master is claimed by this experimental path.

## Native MLT default sequence and exact revision-bound entity selection

After the native Motion Canvas grouping fix, the real E2E reached MLT semantic editing and found an unrelated previously hidden assumption: the pinned Semwright MLT Project::new constructor creates a legitimate default **Main** sequence. A subsequent Motionwright sequence.create therefore yields at least two sequences; an entity list count of one is not the provider contract. Motionwright now explicitly requests a bounded complete page (limit 100), verifies that reported total equals the number of returned entries and cursor is null, then reselects the **unique exact canonical name** at the current project revision. No first-item assumption, cross-project reference reuse, duplicate name, incomplete page or stale revision is accepted. The original source/clip timing checks, no-overwrite media receipts and fail-closed mutation behavior remain unchanged. This repairs only the semantic reference lookup, not audio muxing or release.

## Verification status

Rust unit/contract tests validate bounded native project references, nonadvancing or stale provider revisions, unique created entity refs, required timeline entity lookup cardinality, and fail-closed metadata. The source plan is covered by separate bounded recipe tests and the existing Semwright single-segment `frames.encode`/AV E2E lanes remain active.

The new **exact-SHA `native-mlt-sequence-e2e` CI lane** seeds 33 real one-frame scenes at 30 fps (each has exact rational duration 1/30 second), renders the canonical 32+1 Motion Canvas partitions through the pinned Broker/Driver Host, assembles them via Semwright's semantic MLT edit API, and checks the resulting 33-frame FFV1 + PCM lossless Matroska (transport PCM only, not an approved sound mix) and every artifact SHA-256. This test must actually PASS for the PR's current SHA before the owner-only method may be advertised as a supported user-facing assembly capability or considered eligible for full product acceptance. Tests of a synthetic provider response are not evidence of an actual assembled video; no product acceptance scenario is promoted automatically.
