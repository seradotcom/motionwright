# Motionwright v0.5 — Original SFX admission for native MLT multisegment cuts

Status: **implemented pre-dispatch contract; final SFX AV mux NOT implemented here.**
Stacked on original source proof (#218), three-scene native H.264/AAC
technical acceptance (#232), and Studio review source contracts (#235).
No new audio engine, renderer, provider, backend or creative approval is added.

## Why this gate exists

The existing MLT multisegment native master path assembles a lossless video-only
FFV1 cut plus a project-selected measured-voice WAV. This is a different
source contract from a scene-bound original-SFX-only master. The old voice
path **must never** omit original SFX cues or describe them as measured voice.
It now rejects a project with authored original SFX before invoking Broker.

The new read-only preflight_native_multisegment_original_sfx_source method
authenticates the original StudioService owner project, revision/generation,
exact native Motion Canvas segment manifests, previously verified FFV1 video-
only MLT output, and actual original source WAV bytes in owner CAS. It then
calls preflight_multisegment_original_sfx_clock to validate a strictly bounded
timeline with no unsupported changes to creative timing.

## Accepted source boundary

- Exact project/version, full scene order, H.264/AAC MP4, Rec.709, 30/1 fps and
  48 kHz stereo PCM16; no selected narration, alternate audio cues or subset.
- Two to 64 owner-manifest-verified native MLT clips. Each starts at the next
  source frame, begins source at frame zero and preserves all authored scenes.
  Grouping whole scenes within a clip is allowed; trimming, reordering,
  stretching or omitting scenes is denied.
- Every original scene has a positive, exact 30-fps duration, starting at its
  originally authored global frame. No partial cut can masquerade as a
  source-aligned sound master.
- A bounded immutable owner WAV, up to 983 frames at 48 kHz. Every scheduled SFX event fits the
  master; source frame N begins at 48 kHz sample N × 1,600. All SFX asset
  identities, original WAV SHA-256 hashes and final PCM mix digest must equal
  the actual StudioService source admission record.
- Verified video-only lossless FFV1 intermediary from the same recipe,
  revision, frame count and owner output root. No transport PCM in video.

The immutable MltMultisegmentOriginalSfxReadiness receipt records scene/cue
IDs, original SHA-256 identities, exact sample clock and versioned Film
recipe. Its scope explicitly says **preflight, NOT native mux evidence**.

## Test and security gates

The coordinator accepts in-process native types, not WebView/LLM filesystem
paths, substitutions or arbitrary job references. Original CAS is rechecked
against the exact owner output before a proof is returned. This is a read-
only operation; no native render or uncertain job is retried.

Rust tests construct real SQLite owner projects, two original imported
stereo PCM16 WAVs, a real CAS source mix and typed native MLT clip recipes.
They accept an intact 360-frame two-scene cut and refuse reordered,
shifted, truncated or missing scenes, false WAV/recipe identity, stale
generation/revision, voice-selected profiles and inexact frame clocks.

## Not claimed

This slice does NOT dispatch driver.mlt-video.av.mux for multisegment SFX or
produce a multisegment SFX MP4. It does not expose a new Studio combined-
master button or override existing permissions. The native 540-frame
three-scene *single Film* original SFX route remains the accepted end-to-end
SFX AV demonstration. A future multisegment mux must reuse the original
pinned Semwright Driver Host and independently prove real H.264/AAC frames,
source PCM, AAC timing and native owner receipts.

Combined narration/music/SFX ducking belongs to separate PR #87. Synthetic
source tones are not licensed commercial audio, LUFS mastering, artistic
sound design, human listening approval or a completed v0.5 product.
