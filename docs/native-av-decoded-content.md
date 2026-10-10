# Decoded native H.264/AAC master acceptance (CI-only)

Motionwright's native AV master production already verifies Semwright Broker/Driver Host provenance, hashes the actual FFV1 and MP4 files, and checks that the job returned a completed native result. Those are necessary but not sufficient for usable media: a valid, correctly hashed MP4 can contain black/static video, silence, or accidentally swapped/mixed audio channels.

The dedicated pinned-source GitHub Actions **Native Motionwright AV master** workflow now additionally verifies the **decoded media content** of its existing two-second 1920×1080 Motion Canvas + MLT H.264/AAC fixture. This is a deliberately bounded, machine-readable regression gate, not an application video backend.

## Deterministic input with two distinguishable audio channels

Instead of a two-second silent WAV, the CI E2E writes a **48,000 Hz stereo signed-16-bit WAV**, 96,000 frames, with a 440 Hz sinusoid on the **left channel** and a 660 Hz sinusoid on the **right channel**, independently tapered at both ends. The generated source is checked before Semwright receives it; its SHA-256 is preserved in the same source/receipt and later compared with the native master.

This is non-copyrighted synthetic test data only. No voice recordings, secrets, external services, customer media or synthetic human acceptance claims are introduced.

## Decoded output invariants

After the normal pinned Semwright Motion Canvas→MLT path produces its H.264/AAC MP4, `tooling/native_av_media_probe.py` runs the system `ffprobe` and `ffmpeg` binaries already installed as explicit dependencies of the **disposable GitHub Actions runner**. The app itself never shells out to them.

The independent checks require:

- **Container/streams:** exactly one H.264 video stream and one AAC audio stream, 1920×1080 video at rational **30/1 FPS**, 60 actually decoded video frames, two-channel AAC at 48 kHz, and approximately two seconds of measured container duration (with a bounded codec delay allowance).
- **Actual audio:** decode AAC to interleaved stereo PCM. Its duration must be close to the source. Spectral measurements must detect meaningful 440 Hz energy in the left channel and 660 Hz in the right, with strong channel separation. A silent, mono-folded, swapped, missing, wrong-rate, or seriously truncated AAC track fails the gate.
- **Actual video:** decode and downsample source frames **0 and 30** to fixed RGB samples. They must have a material pixel difference above a stable noise threshold, proving that the pinned Motion Canvas fixture's known linear tile movement survived encoding rather than becoming a still frame.
- **Source identity:** use the same MP4 artifact and source WAV already verified by SHA-256 in the native coordinator. Re-hash the files in bounded chunks and require the probe's digests to equal those recorded in the E2E receipt.
- **Evidence:** write `verification/native-av-master-e2e/<exact-Motionwright-SHA>/decoded-media-proof.json` with measured codecs, frame counts, sample counts, channel tone amplitudes, two decoded-frame SHA-256 digests, and an explicitly narrow evidence scope `actual_decoded_h264_aac_two_second_fixture_only`. The CI evidence verification step rejects missing, mismatched or fake probe results.

The decoded-media probe bounds MP4 size to 512 MiB, PCM readback to 2 MiB, and RGB readback to two 160×90 frames. Decoder commands are exact, argument-list invocations with bounded timeouts and no shell injection. The WAV source itself is ~384 KiB. The probe does **not** attempt unbounded full-video buffers in application memory.

## Adversarial regression suite

The lightweight source-policy job also runs `tooling/tests/test_native_av_media_probe.py` (10 independent test methods): real generated audio, silent/truncated PCM, channel swap, identical left/right audio, malformed stereo samples, wrong video/audio codec or frame rate, wrong duration, duplicated streams and static/corrupt video. Synthetic data tests verify the test's *ability to reject failures*; they do not substitute for the real decoded MP4 run.

## Explicit limits

This check establishes **presence, format and coarse content identity** for one known two-second pinned fixture. It does not certify perceptual video quality, production grading, lip-sync, audio mastering loudness/true-peak thresholds, precise AV sync, streaming playback, client-device decoder compatibility, captions, creative direction, signed distribution, or multi-segment source completeness. Those require separate source-specific and independent product acceptance. All 60 private product acceptance scenarios remain `NOT_RUN` until actually executed and reviewed.

For the full authority chain, see [native production](production.md), [verified local MP4 delivery](master-verified-export.md) and [independent review intake](acceptance/REVIEW_INTAKE.md).
