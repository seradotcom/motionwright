# Motionwright v0.5 — authored transcript to native burned-in caption preview

**Status: bounded implementation; native end-to-end pixel approval pending.**
This does not claim accessible typography, automated speech recognition, proven
caption/audio synchrony or a releasable H.264/AAC master.

## One canonical source, not a second compositor

Deliver > Caption sidecar > Burn-in is an existing saved per-profile
choice. It now opts the same canonical Motion Canvas Film projection into
native Caption subjects and timed Semwright CueGraph data.
The saved project revision, editable shots and selected profile remain
authoritative. The typed Film is passed through the existing
ProductionCoordinator and pinned Semwright Native SDK; there is no
FFmpeg drawtext, independent browser compositor, generated screenshot,
or parallel render engine. WebVTT/SRT export remains separate.

The native Film reuses transcript_for_profile, including **scene cut
rebasing**, so its cue times and text derive from the same owner
transcript as the sidecar. Source identity is the explicitly selected
measured WAV voice track and its immutable matching project audio-asset
SHA-256. Measured alignments to another voice digest are refused; manual
alignment remains explicitly authored rather than independently verified.
Realization reruns native Semwright temporal/caption validation and
fails if the frame count changes.

The opt-in projection refuses, without rounding or silently removing data:

- Missing/wrong voice or asset digests, UNKNOWN transcript timing, a stale
  measured alignment source, unsupported output or non-frame-aligned cues.
- Cross-shot, cross-beat and non-Motion-Canvas cue intervals; overlaps and
  any cue outside the measured voice duration.
- More than 96 cues for a project, more than 32 per shot, more than two
  lines or 42 Unicode code points per line, and control characters.

These limits are a temporary native authoring grammar, not a standard
for caption comprehension. They do not prove shaping, bidirectional text,
contrast, occlusion, font coverage or safe viewing on physical devices.
Native output should be sampled at both cue boundaries and the corresponding
PNG/text readback independently compared before enabling final delivery.
The pinned MLT AV master intentionally **continues to refuse**
burn_in_captions; this work does not remove that safeguard.

## Compatibility and test evidence

Profiles with Burn-in disabled yield their original Film unchanged, even
if other transcript text changes. Caption subjects are separate editable
semantic Film text objects; original Canvas IDs, geometry, cuts,
assets and frame counts remain intact. Imported WAV metadata does NOT
turn into an unregistered visual media asset.

The native_caption_burn_in Rust integration suite checks two scenes,
source SHA and native cue graph timing; matches captions to WebVTT/SRT
source times; tests a rebased single-scene cut and zero-caption mode;
and rejects adversarial unknown, overlapped, out-of-frame, stale-source,
unsupported-renderer and oversize inputs.

GitHub Actions builds against the exact pinned Semwright revision and
runs these Rust tests. This source-level test is not a real burned-in
frame or independent pixel/aesthetic acceptance. The original 60
owner-creative acceptance cases remain NOT_RUN; release remains false.

## Real native pixel regression — strict, source-pinned and isolated

A separate GitHub Actions workflow, V05 source-bound native caption pixels,
provisions the exact SOURCE_LOCK Semwright daemon, Driver Host and Motion
Canvas runtime on a disposable Linux runner. It performs the existing
60-frame *original* project render (including independently decoded
direct-vs-mediated native pixel parity), then CAS-imports a real measured
synthetic WAV and manually authored caption times into a **new revision**
of the same project. The second render has its own request identity and
is never a retry of the first request.

The independent FFmpeg PNG decoder checks that original pixel contents
outside the allowed caption region remain identical, the non-caption
frames 0 and 59 are entirely unchanged, and the two active caption frames
22 and 42 exhibit bounded nonzero RGB changes in the expected lower
area. Every inspected PNG is rehashed and checked against the exact
native artifact manifest. A missing caption, uncontrolled UI layout
change, incorrect cue timing or tampered byte stream fails the job.

Public artifacts contain only exact-SHA technical outcomes and bounded
changed-pixel counts; raw source audio, private Broker/Driver receipts,
original PNGs and transcripts remain in the disposable runner.
All source checks and Rust tests are distinct from actual native pixel
evidence. Until the exact head run passes, the pixel conclusion is
**NOT_VERIFIED**. Even a PASS is not an end-to-end captioned MP4/AAC
master, authenticated speech or a human legibility approval.

### Native text layout isolation

In pinned Motion Canvas, a top-level Txt subject can participate in the
shot root's flow even when its opacity is zero. Each caption is therefore
a native editable text subject nested inside its own fixed, layout-disabled
Group wrapper, rather than a flattened rendered overlay or a top-level
flow participant. This keeps the original producer's object geometry
stable while permitting native DOM font shaping on the nested text.
The pixel comparison rejects any unintended baseline-frame difference.
