# Original SFX clocks for native MLT cuts

The original SFX preflight checks whether an authored soundtrack still matches
an unchanged native MLT edit. It is a source-admission result. The existing
`assemble_native_mlt_multisegment_sfx_master` route separately verifies its
native visual inputs, invokes the admitted mux, and inspects the output.
Passing the preflight alone does not demonstrate that a master was rendered.

## Source and time contract

The bounded profile uses all authored scenes in their original order, exact
30/1 fps, Rec.709 H.264/AAC MP4 output, and 48 kHz stereo PCM16 input. It has
no selected narration, additional audio cues, scene subset, or burned-in
captions. Sidecar selection remains separate from the soundtrack.

The edit contains two to 64 clips. Each clip starts at the current source
cursor, begins at source frame zero, and retains the full duration of its
scenes. A clip may group consecutive whole scenes. Reordering, shifting,
trimming, stretching, inserting, and omitting source frames are rejected.

Frame N begins at sample N * 1,600. Each original cue must resolve to the
same scene, imported asset, source digest, and absolute sample interval.
The mixed WAV uses the same six MiB limit as the existing source preparer:
a 44-byte header and 6,400 bytes per video frame allow at most 983 frames.

The source preparer reads the selected assets from the current project's
CAS, runs the existing source-bound mixer, and writes one immutable,
digest-named WAV under the trusted output root. Verification compares its
actual bytes with that same current source mix. A changed generation,
revision, recipe, asset set, source digest, or output file invalidates the
source proof. Existing fields named `exact_pcm_sha256` and
`exact_mix_pcm_sha256` carry the digest of the complete canonical WAV,
including its header; they are not a separate raw-PCM digest.

## Native handoff

The coordinator first checks its current owner connection and project
stamp. It binds the complete native segment manifests, the corresponding
verified lossless FFV1 edit, and the original CAS soundtrack before native
muxing. The source preflight never grants permission, replaces source
identity with a browser path, or authorizes a retry of an uncertain job.

A final master needs its own original job evidence and independent
inspection of every decoded frame, the actual audio, their clocks, and
the declared codec/profile. Source admission does not establish native
source reopening, NLE reception, aesthetic acceptance, external rights,
loudness compliance, or listening approval.

## Focused regression cases

`crates/native/tests/semantic_sfx_multisegment_clock.rs` exercises four
source cases against the current implementation:

1. A real SQLite project imports two generated stereo calibration WAVs
   into CAS and prepares its unchanged 360-frame soundtrack. The test
   verifies the physical WAV digest, 576,000 stereo sample frames, and
   silent and audible windows on both sides of the scene boundary.
2. Reordered, shifted, trimmed, missing, or shortened clips are rejected.
3. Stale project or recipe identity, altered digests, changed source
   bindings, and physical corruption of the prepared WAV are rejected.
4. A selected voice, partial scene selection, fractional scene clock,
   incompatible frame rate, or reordered source scenes is rejected.

The typed video recipes in these tests are controlled inputs, not actual
native manifests or fabricated successful renderer receipts. The tones
are original calibration signals, not narration, licensed music, a
loudness reference, or an aesthetic verdict. No native renderer, mux, or
uncertain job is invoked by this suite.

Run only this source suite when changing this contract:

```sh
cargo test -p motionwright-native --test semantic_sfx_multisegment_clock
```

A change to native production, source preservation, or the final medium
also requires the corresponding separate native gate.
