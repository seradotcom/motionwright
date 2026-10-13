# Motionwright v0.1 — public native MLT evidence boundary

**Scope: CI evidence publication only.** This change does not create native
projects, retry mutations, approve cleanup, imply live Broker resources, or
authorize stable v0.1.

## Risk and implementation

The previous v0.1 native MLT CI matrix uploaded its **entire working directory**
on success or failure. It contained semwrightd.log, project lifetime
inventories, provider-native job references, path-bearing timeline evidence and
raw result receipts. These are not needed in downloadable public artifacts.

The existing CI task still runs the pinned native Semwright Broker/Driver Host,
verifies 33 decoded frames, two authored video segments, exact FFV1 transport
hashes and final measured H.264/AAC audio when applicable. The independent
publisher tooling/native_mlt_public_artifact.py runs **after** those tests and
writes a separate public-only directory.

| CI mode | Public native files | What they demonstrate |
|---|---|---|
| pcm | native-two-segment-lossless.mkv | 33-frame FFV1 plus PCM transport, not a final master |
| video-only | native-two-segment-video-only.mkv | 33-frame FFV1 video without audio |
| final-mp4 | native-two-segment-video-only.mkv, native-multisegment-master.mp4 | Source FFV1 intermediate and H.264/AAC measured-voice sample, not human approved |

Only public-evidence.json is added. It binds source SHA, Semwright pin,
matrix mode, file sizes and individual SHA-256 hashes; marks owner consent,
native cleanup, installed-owner acceptance and release as NOT_VERIFIED or
false. No original lifetime inventory, native job reference, session, daemon
log, source mount path, secret, private receipt or traceback is copied.

This publisher validates source SHA/pin, exact matrix mode and content scope,
source frames [32,1], 33 assembled frames, FFV1 mode and audio transport,
the non-public lifetime inventory's exact project/generation/revision,
no native cleanup claim, MKV format and hashes, and, for final-MP4,
H.264/AAC, measured voice and independently decoded stereo-content evidence.
Full CI conformance still takes place before publication; basic container
header inspection is not a media decoder.

On failure/cancellation the public folder contains only a sanitized
NOT_VERIFIED receipt and **zero media files**. GitHub Actions retains the
failure status and does not silently convert it to success.

## Validation and remaining blockers

The dedicated Python suite covers all three modes, forged source and pin,
unexpected native media, hashes, altered generation, forged cleanup,
incorrect audio evidence, linked or nested paths, duplicate JSON keys
and failure-only redaction. A separate local compatibility smoke on an
existing successful real native MLT run admitted exactly one MKV for PCM,
one for video-only, and MKV plus MP4 for final mode. This does not replace
the required **new exact-head GitHub Actions E2E**.

The feature does not grant or prove owner-foreground native MLT cleanup
(issue #91), resolve Driver Host generation loss (#49), remove the Linux
glib advisory (#89), or complete the independently reviewed v0.1 release
and installed-owner acceptance (#126).
