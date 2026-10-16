# Motionwright v0.5 — three-scene original semantic SFX to native H.264/AAC

Status: **bounded implementation with independent CI acceptance pending.**
This is a stacked v0.5 expansion on original semantic SFX PR #218, not a
replacement backend, full musical sound design or product release.

## Source-based creative composition

A real authored, editable 18-second native Film includes **three contiguous
six-second scenes at 30 fps**:

1. Original generic ProductStage, with all authored editable UI objects
2. Original deterministic procedural-field graphics, intentionally **silent**
3. Original measured-data source with independently source-locked numeric glyphs

The existing native composition seed and exact pinned Semwright Driver Host
render the actual 540-frame Film. This new acceptance fixture adds two
original owner-generated stereo PCM16 WAV assets through StudioService CAS,
anchors semantic SFX at **scene 1 / frame 6** and **scene 3 / local frame 20**,
and obtains global frame anchors **6 and 380**, respectively. Their exact
48 kHz PCM starts are **9,600 and 608,000 samples**. Each original WAV lasts
24 frames; the absolute tails end at frames 30 and 404.

No sound is authored during scene 2. This intentional absence is just as
important as the audible events: the original PCM and the final decoded
AAC must show no stretched tone or residual sound in the middle scene.

## Authoritative processing

1. The existing project seed creates editable original Stage/procedural/
   measured data and a full 18-second native deliverable in SQLite.
2. A CI-only source helper imports the **actual original stereo WAV bytes**
   through the current owner CAS, records exact SHA-256 and versioned events,
   and verifies source schedule addresses before any native render.
3. The existing native Film projector reconstructs the precise Film both
   before and after adding SFX. Only the two SFX WAV assets are removed
   from the visual-only clone; original measured-data evidence and every
   editable visual object remain unchanged.
4. The existing owner-native source mixer generates an exact **864,000-frame**
   stereo PCM16 WAV (3,456,000 bytes of PCM), with unchanged gain/attack/
   release envelopes and signed-sample clipping refusal. The entire WAV
   must agree to within 1 PCM LSB with independent reconstruction from the
   two imported stems. The digest-addressed owner WAV is the actual input.
5. The same Semwright Native SDK/Broker Motion Canvas renderer produces the
   source-bound 540 native frames. The existing MLT Driver Host creates the
   original H.264/AAC MP4; no direct ffmpeg product renderer is added.
6. The already-established editable three-scene visual inspector reads the
   actual original 540 native PNG frames and a separate set of frames decoded
   from the FINISHED H.264 MP4 at indices 140, 270 and 480. Both must preserve
   the original ProductStage, procedural source geometry and measured-data
   bar/glyph properties. The pre-SFX native Film SHA and post-SFX Film bytes
   must match; the archived pre-SFX source is not a substitute for the
   current owner's audio CAS generation/revision.
7. CI-only independent FFprobe/FFmpeg readers verify the 540 decoded H.264
   frames, codec/geometry/audio clock, original 440/660 Hz and 740/980 Hz
   stereo channel identities, early silence, complete middle scene silence
   and post-cue release. Each actual AAC cue onset must be within one
   1,024-sample AAC access unit of its authored first PCM sample.
8. Public media and a bounded exact-commit manifest are published only after
   all source/mux/decoder checks pass. Failed or uncertain native work produces
   a NOT_VERIFIED-only artifact, without private provider receipts, owner
   paths, job references or false creative approvals.

## CI and independent negative evidence

Workflow: .github/workflows/semantic-sfx-multiscene-native-av.yml

Source-only checks: tooling/tests/test_semantic_sfx_composition_av_evidence.py
and crates/domain/tests/semantic_sfx.rs.

Independent decoded media and original source manifest:
tooling/semantic_sfx_composition_av_evidence.py.

Original 60-frame two-source SFX acceptance stays independently runnable,
with its first/second-source overlap tests and source hash policy unchanged.
The new project is **540 frames in a single 18-second native Film run**,
not evidence for a multisegment MLT transition/concat pipeline, arbitrary
clips or independently mixed music/dialogue.

## Remaining work — not claimed

These are source-original numerical tones, **not** commercially licensed
sound effects or finished artistic audio. The fixture does not prove external
music/narration ducking, intelligibility, LUFS/true-peak mastering, real
customer media rights, font accessibility, device playback, listener quality
ratings or full 60-case owner human acceptance. The separate voice/music/SFX
mixing and ducking PR #87 remains the owner of that capability.

A CI PASS is a bounded technical fact for the exact source SHA, not approval
of a creative product release. The v0.5 requirements status remains partial,
and previous 60-frame or composition-only CI evidence must not be substituted
for this new exact project revision.


## First exact-SHA CI and independent video proof hardening

Commit b68b74a rendered, mixed and completed its source-bound H.264/AAC
acceptance, but the final CI assertion failed AFTER the native run: it
looked for verification/semantic-sfx-av-e2e/<SHA>/result.json (the
single-scene historical fixture) instead of the multiscene source output.
This was a CI evidence-path defect, not a confirmed renderer/mixer error.
The new exact-evidence path is regression-checked against the public
artifact publisher's multiscene root.

The improved independent gate additionally reuses the **existing**
three-scene native visual pixel oracle, not a hand-authored replacement:
frames 140, 270, and 480 must preserve the original first-party ProductStage,
procedural geometry and measured label/bar, in BOTH Native SDK PNG source
frames and frames independently decoded from the actual H.264 MP4.
The original project bundle stays immutable at the pre-SFX revision; a
separate byte-identical post-SFX Film parity proves that importing two
WAVs did not alter any visual scene node or measured-data source.
Public success requires matching stage, procedural and numeric pixel evidence,
native Film digest, finished MP4 SHA, original stereo WAV SHA, exact PCM mix,
48 kHz sample windows and the two decoded AAC onsets.

No public media or positive visual/audio certificate may survive a later
validation error. The existing fail-closed curator discards all artifacts and
revokes partial visual success keys if any source/decoded-media test fails.
These checks remain synthetic technical acceptance, not professional
visual/audio approval.
