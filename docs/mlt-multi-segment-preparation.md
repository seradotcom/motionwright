# Canonical MLT multisegment preparation and exact edit recipe

Motionwright can render native Motion Canvas segments and produce a source-bound read-only multisegment preflight. This work introduces the next bounded production stage, **without creating a final composited master**.

## Deterministic semantic edit recipe

`motionwright_native::mlt_edit_plan::mlt_native_video_recipe` consumes the exact result of the previously implemented [multisegment preflight](multi-segment-preflight.md), not a guessed clip list. It translates its segment identities, frame offsets and verified manifest hashes into a canonical **MLT timeline source recipe**.

The recipe defines: the exact rational MLT profile, project/sequence/track identity, sequential half-open clip ranges (`source_in=0,source_out=frame_count`), exact start frames, stable frame-encoder outputs, and the known pinned render profiles (`lossless` FFV1 source and `h264-aac-mp4` final mux). Its artifact basename is a SHA-256-derived namespace from the source preflight. It rejects repeated scene IDs, duplicated jobs, modified frame ordering, foreign artifact paths, missing manifests, unsupported aspects/FPS and frame count discrepancies rather than silently moving clips.

This is **not executable user-authored JSON**: the function exposes no arbitrary command name, executable, MLT expression, filesystem root or path supplied by the WebView. The recipe's source references can only come from Motionwright's native, owner-verified manifest preflight.

## Native FFV1 video sources through the pinned Driver Host

`ProductionCoordinator::prepare_multi_segment_ffv1` is an internal owner-only method. It reopens the current authoritative creative project and **re-executes** the native source preflight inside the owner output root, then calls the existing pinned Semwright Broker/MLT Driver Host **`driver.mlt-video.frames.encode`** for every segment. That already-certified driver operation must read and verify every real PNG source referenced by the manifest before producing a video-only FFV1 Matroska.

For each result Motionwright verifies the native provenance through the existing `ProductionCoordinator::execute` path, then requires the FFV1 codec, Matroska container, precise frame count, resolution, rational FPS, video-only media flags and **exact expected output path**. It validates the SHA-256 of the actual encoded file beneath the owner output root and cross-checks its byte count. Each FFV1 artifact is bounded to 512 MiB, and all encoded sources together are bounded to **1 GiB**. Production request IDs remain stable per segment; an uncertain or failed mutation refuses silent redispatch.

The coordinator checks project generation and revision **before and after** preparation. If an editor modifies the creative project in between, the returned result fails stale. Every intermediate artifact is still associated with its original creative revision and native source segment IDs.

The receipt `MltPreparedMezzanines` returns the exact segment index, output start frame, frame count, source native job, FFV1 relative path, digest and bytes, along with an explicit evidence scope:

`real-source-ffv1-segments-not-composited`

**That is not an H.264/AAC master**. The stage does not yet create a Semwright semantic MLT project, import these FFV1 sources as assets, insert the clips onto a real video track, run the MLT sequence renderer, combine the validated 48kHz stereo WAV or publish a final MP4. These operations require a separate authorized end-to-end MLT job, deterministic revision-bound reference updates, actual media inspection and a real multi-segment E2E before the desktop may claim completed output.

## Verification

Rust source tests cover deterministic stable recipe names and exact edit intervals, invalid/cross-project paths, duplicate segments/jobs/scenes, mismatched durations/rates and unsupported MLT profiles. Separate native production tests check that only **video-only FFV1** with the expected frame count/FPS/dimensions, source output path and size passes the Semwright receipt boundary.

Actual single-segment `frames.encode` and `av.mux` execution are covered by existing pinned Semwright/MLT AV-master E2Es. The new multisegment preparation **does not yet have a separate 33-scene real-driver run**, and no independent product acceptance scenario is marked PASS. The extension is deliberately staged without adding a fake media success.
