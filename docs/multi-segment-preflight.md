# Multi-segment Motion Canvas → MLT source preflight

In Deliver, render the saved Motion Canvas cut or reopen its original capture, then choose **Check native sources**. After the source check passes and the delivery profile has a compatible measured voice take, choose **Assemble multi-segment MP4** and authorize the render. The master uses [deterministic MLT FFV1 preparation](mlt-multi-segment-preparation.md) and [sample-precise audio checks](mlt-multisegment-audio-readiness.md).

The desktop check invokes `motionwright_native::assembly::preflight_multi_segment_mlt` through the trusted native session. It reports the source segments and frame counts. Mastering has its own effect grant and validation.

## Admitted source set

The operation accepts exactly the current versioned Motionwright project, saved deliverable, explicit `FilmBuildOptions`, Motionwright-owned `MotionCanvasRenderEvidence` returned after verified Semwright native production, and the **owner-supplied output root**. It reprojects the exact current Film partition rather than trusting a caller-supplied segment list. It requires:

- 2–64 consecutive source-bound Motion Canvas segments with the **same ordered scene identities**, per-segment frame counts and segment IDs as the freshly compiled canonical Film. A saved alternate cut is honored in its declared order.
- **Motion Canvas scenes only.** The [Linux hybrid route](owner-native-runtime-onboarding.md) verifies Motion Canvas and Manim contributions separately.
- An exact project ID/generation/revision, output profile, rational FPS, and nonempty distinct native job refs. A segment cannot be silently substituted, reordered, omitted or duplicated.
- Exactly frame-aligned output starting offsets, with **no implicit timeline gap or overlap**, total **at most 36,000 frames** and nonzero frame count for every segment.
- A saved Rec.709 MP4 profile with H.264 video, AAC/48kHz audio and **1920×1080 or 1280×720** output. Those are the pinned MLT semantic render's H.264 presets; portrait/square are not silently converted to landscape.
- Each native segment's Semwright verification report shows `execution_status=completed`, `support_level=native`, no unresolved findings and a nonempty all-PASS set of native validation checks.

## Output-root manifest checks

Before returning the typed `MultiSegmentAssemblyPreflight` record, the function follows each canonical `render-<32-lowercase-hex>/artifact-manifest.json` **under the already authorized output root** and independently verifies actual manifest bytes against the claimed SHA-256. Its manifest plan must match the exact profile dimensions, FPS numerator/denominator, frame origin, frame count and contiguous indexed PNG metadata. The input manifest path cannot be arbitrary, symlink-traversed or modified without detection.

The preflight checks manifest digests and reports `manifest-digest-verified-not-composited-mp4`. The mastering operation calls the pinned Semwright `frames.encode` for each segment to reopen and hash its indexed PNGs. The completed MP4 requires its own verified native output.

No user-editable input can mint a signed or native job result. The desktop command `preflight_multi_segment_mlt_readiness` now obtains the original `MotionCanvasRenderEvidence`, exact Film authoring options and canonical output root **only from the trusted private preview registry**, using an opaque session token minted after the actual Semwright render. The WebView request accepts project ID/generation/revision, saved profile and that token; it cannot supply arbitrary evidence, hashes or filesystem paths. The response exposes only source-scoped segment IDs and frame offsets, never owner filesystem paths or provider payloads.

## Tests

The native crate's synthetic fixtures construct 33 saved Motion Canvas scenes (partitioned exactly into **32 + 1** by canonical Film), two source-bound receipt-like render evidences, and real on-disk manifest bytes. Tests check an exact 960+30 frame output cut, output profile, digest verification, stable scene IDs and a correctly labeled non-media result. Adversarial cases reject altered creative revision, reordered or duplicate job evidence, false native checks, tampered manifest bytes, missing segments, mixed renderers and an unsupported portrait output.

The manifest tests cover source checks with synthetic fixtures. Native rendering, output verification and independent product acceptance have their own checks.
