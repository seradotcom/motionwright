# Motionwright v0.5 — original SFX WAV waveform in installed Studio

Status: **new source-bound read-only observation awaiting exact-HEAD CI**.
This feature is stacked on the original-semantic-SFX source/publisher PR #218
and Studio's 540-frame review contract PR #235. It is not a new audio player,
provider, synthesis service, rendering backend or creative approval.

## User workflow

In the installed Studio Audio workspace, every **saved** original semantic
SFX cue offers **Inspect WAV**. Clicking the control reads the exact
StudioService content-addressed imported source associated with that cue,
then displays separate left/right waveform measurements at each authored
30-fps frame bucket.

Each bucket represents **1,600 real PCM samples per channel** at 48 kHz.
For each channel it records the exact least and greatest signed 16-bit
sample, preserving extreme negative samples and zero values without a fake
normalized waveform. The panel reports the absolute PCM16 peak (0–32,768),
full original WAV digest, selected source duration and a SHA-256 of the
deterministic measurement record. It provides a source-frame selector and
seeks the existing project timeline to that source's absolute authored
frame. It does not play the sound or claim the finalized MP4 has been heard.

The waveform is **not** LUFS, intersample true peak, compressed AAC analysis,
intelligibility or musical/loudness mastering. It reflects only the original
imported PCM bytes before the cue gain, attack, release, overlapping sounds
and native mux. Those still require the existing independent output checks.

## Source authority / privacy

A new **read-only** desktop command, original_sfx_waveform, accepts ONLY:
project ID, generation, revision and a saved semantic SFX cue ID. It
forbids WebView-provided filesystem paths, arbitrary SHA digests, raw PCM,
native job references and unknown request fields. The worker reopens the
original project revision and calls the existing owner-CAS verified WAV
source method. The source bytes never enter WebView IPC.

The same strict native RIFF parser as the actual SFX mixer requires one
canonical WAVE PCM16, **48,000 Hz, two channels, exact 44-byte header** and
the exact authored source length, with no extra data, compressed audio,
implicit resampling, trimming or sample generation. The entire WAV must
hash to its original imported SHA-256 before any waveform measurement.

A result is valid only for the matching project generation, revision, cue,
source asset ID, digest, exact 30-fps source length and bounded PCM16
extrema. Any async read returned after a creative source edit or revision
change is hidden. In public browser mode the API returns no waveform
instead of synthesizing a dummy graph.

## Validation

Rust integration tests import a **real numerical WAV** through the owner
SQLite + content-addressed source path, read it back at the exact revision,
verify left/right extremes in three independent 1,600-sample buckets, and
refuse foreign SHA, wrong RIFF, shortened/trailing bytes, a different
source ID or authored source-length change. No renderer or native Broker is
dispatched by source observation.

Studio unit tests reject stale/foreign/version-mismatched proof, missing
channels, forged extrema, changed input digests and nonintegral source
frame addresses. Browser IPC acceptance checks cue-scoped requests contain
no filesystem path or PCM blob, displays real measured extrema and refuses
to invent waveform data without the installed desktop.

Full Rust native/desktop, Studio build and browser suites remain CI gates,
not a release or human acceptance certificate.

## Not covered

This is not a combined voice/music/SFX waveform, no professional audio
quality evaluation, no external rights verification, no new clip renderer,
no waveform transcription and no new permission grant. The separate
combined-audio mixing PR #87 remains the owner of ducking/loudness.
