# Motionwright v0.5 — native caption source to measured H.264/AAC

**Status: bounded implementation in branch, independent exact-head CI pending.**
Not a source of verified human legibility, customer rights, commercial release
or arbitrary timeline support.

This extends the already-pinned Semwright Native SDK Motion Canvas
Caption/CueGraph output to the first-party MLT Driver Host video
intermediate and native H.264/AAC mux. It does **not** use FFmpeg drawtext,
create a second compositor, crop or edit an existing timeline, synthesize
speech, overwrite the user's original assets, or grant publication rights.

## Canonical source chain

1. A persisted delivery profile must explicitly enable captions and Burn-in,
   select one measured stereo 48kHz WAV, use Rec.709 H.264/AAC MP4,
   and match the exact project generation, revision and output frame rate.
2. Source-authored transcript cues are selected/rebased using
   transcript_for_profile, must match the selected measured voice digest,
   be non-UNKNOWN, source-ordered and nonoverlapping, exact-frame aligned,
   bounded (1–96 cues), and no longer than the measured WAV.
3. The **same native Film** is constructed by the existing
   build_motion_canvas_segments. Every cue must fit a single native shot,
   with real source-bound Text subjects and CueGraph scheduling.
   Semwright's exact-frame native verification must PASS before proceeding.
4. Existing Semwright MLT Driver Host encodes the SHA-verified frame manifests
   to two or more source-bound FFV1 clips, constructs the semantic cut, outputs
   a video-only FFV1 and muxes the verified measured WAV as H.264/AAC MP4.
   All original hashes, frame count, sample rate, audio channels and codecs
   remain checked. The established no-burn-in path is unchanged.
5. New optional caption_cue_count and a transcript-digest-linked readiness
   fingerprint make a captioned source impossible to confuse with the plain
   output. The native master returns a distinct, explicitly **not human-
   or decoded-pixel-approved** scope until independent readback succeeds.

## Independent original-media acceptance (CI)

A dedicated captioned-mp4 mode in the existing MLT multisegment CI uses
one source-verified 33-frame / 30fps project split into exact 32+1
Motion Canvas segments. Two source-authorized but **synthetic** captions
live inside single-shot intervals [15/30,16/30) and [32/30,33/30);
the measured 48k stereo WAV is an original two-channel synthetic tone,
not actual speech.

Independent FFmpeg inspection (CI evidence only, never production compositor)
verifies actual AAC source content, stream layout, full master H.264,
and decoded lossless FFV1 vs lossy MP4 pixels at frames 0, 14, 15, 31, 32.
A bounded mask derives true caption-only high-contrast changes from
the source FFV1, then requires the decoded MP4 to retain those glyph
pixels and also preserve an inactive frame. It fails if the original
caption never drew, the MP4 omitted it or the FFV1/source identity was
modified. The original private owner output stays on the disposable
runner; only bounded public evidence and explicitly synthetic test
media are eligible for CI publication.

The final H.264 and its audio cannot be considered delivered merely
because a provider returned succeeded. Neither a successful
unit test, CI media nor any static typography heuristics proves
human readability, alignment with authentic recorded speech, real
device subtitles, accessibility across languages/fonts or license
rights. The 60 independent creative owner acceptance cases remain
NOT_RUN, and v05_release_ready=false.
