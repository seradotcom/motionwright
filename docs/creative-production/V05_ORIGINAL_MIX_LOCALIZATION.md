# Motionwright v0.5 — Original voice/music/SFX technical mix

**SRS scope:** `MW05-E08-04 — Mix y localización`. The feature is a first-party, native Rust **source-bound PCM mixer** with independent EBU R128 measurements. It uses the existing Motionwright Project audio state, exact DeliveryProfile and measured VoiceTrack; it does **not** replace the timeline/scheduler or implement a parallel audio engine.

## Native contract

`crates/creative-library/src/mix.rs` provides `OriginalMixPlan` and `render_original_source_mix`:

- Three distinct owner-registered audio assets: measured voice, music and SFX, all 48 kHz stereo signed 16-bit PCM source with SHA-256 over the actual decoded bytes.
- The active measured voice track must refer to its original project asset. Delivery language, output sample rate and optional voice-track pin must match an **existing** DeliverableProfile. Inputs belong to the exact project/generation/revision; a missing or stale source digest is denied.
- Ducking windows are sample-accurate. They must be exactly the manually timed (or sufficiently high-confidence measured) transcript segments in the **existing** `Project.audio`. Unknown or low-confidence ASR cannot silently establish spoken content or drive automation.
- Voice and music gains come from existing domain `MixIntent`. SFX gain, music duck attenuation (0–30 dB) and linear attack/release (1–48,000 samples) are explicit. Changes cannot reassign source asset identity, round a fractional sample, extend the clip or cross a voice window without failing validation.
- The mixer produces actual deterministic interleaved PCM samples, with bounded duration/byte budgets and a configurable **sample peak guard**. It rejects clipping instead of silently imposing a limiter or claiming that a target LUFS automatically guarantees intelligibility.
- The original source-only evidence includes SHA-256, measured PCM RMS/peak, ducking profile and declared performance. **It expressly leaves EBU R128 LUFS, intersample true peak, intelligibility and creative/release approval as unverified** until independent measurement/listening.

The human author remains responsible for original voice and stem rights, level design, accurate transcription, target profiles, source-medium checks and creative approval. An authenticated owner lock is not produced by this module.

## Disposable source-bound E2E

`tooling/hyperframes/original_mix_e2e.py` synthesizes **three original instrumental/stem studies** in English, Spanish and German DeliveryProfile contracts. Their apparent vocal bus is a **sine-wave tone proxy, not speech**, and must never be interpreted or marketed as localized spoken narration. Those test artifacts are intentionally synthetic, unmastered and not cleared commercial music.

The test creates 2.4-second, 48-kHz stereo source PCM for each locale. Two manually specified sample intervals drive ducking of the original music bus. Rust renders the three WAVs and retains both original source fingerprints and the exact authored plan. The CI then:

1. Independently decodes the actual WAV with FFmpeg and requires **byte-identical PCM** against the Rust output SHA-256.
2. Measures real integrated loudness (**LUFS**) and intersample **true peak (dBFS)** using `ebur128=peak=true` on the written files. Reported numbers are *measurements*, not an automated normalization or certificate of artistic quality.
3. Proves that the music gain is attenuated by the explicit ducking contract during source-reviewed windows and recovers outside those windows, using the actual mixed PCM and the original independently hashed music/voice/SFX samples.
4. Produces source-bound WAVs, waveform PNGs and structured JSON receipts for a human audio editor to inspect and listen to; artifacts disclose `human_listening_review: NOT_PERFORMED` and `mastering: NOT_PERFORMED`.

Tests include missing/wrong source digest, non-active/foreign voice, unapproved ASR alignment, invalid localized profile, stale project revision, unauthorized gain and clipping attempts, overlapping duck windows, noncanonical sample times and forged approval fields. Heavy processing remains on GitHub Actions, not the owner's workstation.

## Not implemented or not accepted

This is a **bounded mixing prototype** for first-party source assets: no automatic high-quality TTS, real speech-language translation, licensed music acquisition, speech intelligibility testing, multichannel broadcast mastering, LUFS target optimization or final third-party audio deliverable. The 15-second PCM budget is deliberate; a long-form production mix requires a validated streaming/chunked implementation with continuous automation and independent QC. The user must still listen to and approve each real delivery profile. The actual project runtime must admit the original PCM source rights and attach the produced audio to a real composed video by the existing media pipeline.

The requirement is **PARTIAL**, not accepted for v0.5 release. A successful synthetic proxy test only supports deterministic gain, ducking, checksum fidelity and independent signal analysis.
