# Multi-segment Motion Canvas → MLT source preflight

Motionwright can produce verified native Motion Canvas segments and independently master one segment through the pinned Semwright MLT Driver. **A multi-segment final MP4 is not yet implemented. The next bounded internal stage, [deterministic MLT FFV1 preparation](mlt-multi-segment-preparation.md), now derives exact clip ranges and uses the pinned Semwright frames encoder to prepare individual verified sources without creating a composited output.** The next required step is to assemble an exact frame-indexed cut from several native segments without guessing source media, flattening unsupported renderers, or silently changing delivery dimensions.

The `motionwright_native::assembly::preflight_multi_segment_mlt` library operation provides the bounded, read-only **source-conformance gate** for that future workflow. It does **not** dispatch MLT `project.create`/`clip.insert`/`render.start`, reencode frames, or generate a finished master. A separate **read-only desktop readiness control** in Deliver now invokes the same preflight through the trusted native session and displays the segment/frame report; it never claims a finished MP4. No new Native SDK mutating capability is claimed.

## Admitted source set

The operation accepts exactly the current versioned Motionwright project, saved deliverable, explicit `FilmBuildOptions`, Motionwright-owned `MotionCanvasRenderEvidence` returned after verified Semwright native production, and the **owner-supplied output root**. It reprojects the exact current Film partition rather than trusting a caller-supplied segment list. It requires:

- 2–64 consecutive source-bound Motion Canvas segments with the **same ordered scene identities**, per-segment frame counts and segment IDs as the freshly compiled canonical Film. A saved alternate cut is honored in its declared order.
- **No Blender, Manim, MLT or other renderer scene** without a separately verified media contribution. Mixed-renderer compositing remains a later admission step.
- An exact project ID/generation/revision, output profile, rational FPS, and nonempty distinct native job refs. A segment cannot be silently substituted, reordered, omitted or duplicated.
- Exactly frame-aligned output starting offsets, with **no implicit timeline gap or overlap**, total **at most 36,000 frames** and nonzero frame count for every segment.
- A saved Rec.709 MP4 profile with H.264 video, AAC/48kHz audio and **1920×1080 or 1280×720** output. Those are the pinned MLT semantic render's H.264 presets; portrait/square are not silently converted to landscape.
- Each native segment's Semwright verification report shows `execution_status=completed`, `support_level=native`, no unresolved findings and a nonempty all-PASS set of native validation checks.

## Output-root manifest checks

Before returning the typed `MultiSegmentAssemblyPreflight` record, the function follows each canonical `render-<32-lowercase-hex>/artifact-manifest.json` **under the already authorized output root** and independently verifies actual manifest bytes against the claimed SHA-256. Its manifest plan must match the exact profile dimensions, FPS numerator/denominator, frame origin, frame count and contiguous indexed PNG metadata. The input manifest path cannot be arbitrary, symlink-traversed or modified without detection.

This checks the **manifests themselves**, not every PNG payload. Actual multi-segment MLT production will need to invoke the pinned Semwright `frames.encode` operation for **every** source segment, which reopens and hashes all indexed PNGs before encoding. Only after a separately controlled MLT timeline assembly and native output verification can the app claim a composited MP4. The preflight deliberately identifies its evidence scope as `manifest-digest-verified-not-composited-mp4`.

No user-editable input can mint a signed or native job result. The desktop command `preflight_multi_segment_mlt_readiness` now obtains the original `MotionCanvasRenderEvidence`, exact Film authoring options and canonical output root **only from the trusted private preview registry**, using an opaque session token minted after the actual Semwright render. The WebView request accepts project ID/generation/revision, saved profile and that token; it cannot supply arbitrary evidence, hashes or filesystem paths. The response exposes only source-scoped segment IDs and frame offsets, never owner filesystem paths or provider payloads.

## Tests

The native crate's synthetic fixtures construct 33 saved Motion Canvas scenes (partitioned exactly into **32 + 1** by canonical Film), two source-bound receipt-like render evidences, and real on-disk manifest bytes. Tests check an exact 960+30 frame output cut, output profile, digest verification, stable scene IDs and a correctly labeled non-media result. Adversarial cases reject altered creative revision, reordered or duplicate job evidence, false native checks, tampered manifest bytes, missing segments, mixed renderers and an unsupported portrait output.

Synthetic manifest fixtures do **not** imply that native PNGs were rendered. The existing true Semwright Broker→Motion Canvas→MLT E2E covers the single-segment AV master; **a separate actual two-segment MLT E2E remains required** before this preflight can be treated as end-user AV delivery capability. The 60 independent product acceptance cases remain separately NOT_RUN.
