# Native multisegment A/V source readiness

Motionwright can already produce verified Motion Canvas frames, prepare per-segment lossless FFV1 sources through Semwright's pinned MLT Driver Host and render a verified **single-segment** H.264/AAC master. The native two-segment video-only timeline is being developed separately. Before that video can be paired with voice audio, the application must prove that the same creative revision, output profile, complete video cut and imported measured audio describe one consistent source.

`motionwright_native::mlt_av_audio::preflight_multi_segment_audio` implements this **read-only, owner-only input gate**. It does not issue a Driver Host command, start a render, mux video with audio, mix music, stretch audio or export an MP4. It is not exposed as a WebView command or a public arbitrary-path operation.

## Source identity and authority

The trusted coordinator must load a current application-owned `Project` from `StudioService`, rebuild the exact `MltNativeVideoRecipe` from owner-verified Film/manifests and obtain `MltPreparedMezzanines` from the canonical Semwright Broker/Driver Host. The corresponding `VerifiedMasterVoice` must come from `StudioService::verified_master_voice` for the project generation/revision and profile-bound `voice_track_id`, never from user/model supplied file paths.

The preflight verifies that these inputs all agree on project identity, generation, creative revision, deliverable ID, full native source input SHA-256, frame count, exact rational FPS and the supported 1920×1080/1280×720 Rec.709 H.264/AAC 48 kHz MP4 profile. It compares each prepared FFV1 file to its original scene IDs, Motion Canvas job, manifest SHA-256, exact half-open source frames, output start and deterministic output path. Duplicate, omitted, reordered or mismatched segments are rejected. For each FFV1 it rehashes the actual file below the owner output root, checks its recorded size and enforces a maximum of 512 MiB per segment and 1 GiB overall.

The selected voice track must refer to the exact imported WAV asset and SHA-256 in the same project; the saved output profile must explicitly bind that track. Only a measured **48 kHz stereo RIFF/WAVE** is admitted. Its current source file is reread in bounded chunks to check a 256 MiB limit and the complete SHA-256 against the CAS identity. The desktop's existing staging/mux boundary remains responsible for detailed PCM/float WAV validation before actual dispatch.

## Sample-precision timing

The video duration is defined by **total native frames × fps denominator / fps numerator**. The recorded voice duration remains the exact measured rational time. With 48,000 samples per second, the preflight checks the difference using checked integer cross multiplication without floating-point rounding or fabricated millisecond timestamps.

A mismatch greater than **one 48 kHz sample** returns `Unsupported`. No padding, clipping, time-stretching or hidden audio alignment is introduced. Fractional frame rates such as 30000/1001 retain their exact mathematical meaning.

Since this path has no gain processing, output music mixing or loudness normalization, it also rejects a profile that requests caption burn-in and any mix state that explicitly requests changed voice gain, target LUFS or target true-peak compliance. Ordinary transcript/cue metadata does not itself become encoded audio.

## Evidence and downstream requirements

The typed `MultiSegmentAudioReadiness` reports project/version, voice asset ID, verified digest/size, native frame total/FPS, segment count and a deterministic source fingerprint. It contains **no absolute CAS paths, raw WAV samples, FFV1 content, Driver Host secrets or media output URLs**.

The evidence scope is deliberately:

`verified-owner-audio-ffv1-inputs-not-muxed-mp4`

Passing the gate is **not** proof that the video-only MLT timeline has been rendered or that a real H.264/AAC master exists. The next independent task is to attach this source-bound audio to a *completed, actually verified* MLT video timeline through the pinned Semwright `driver.mlt-video.av.mux` capability, with one-time owner grants, stable mutation IDs and real native E2E verification of decoded audio/video. The video-only timeline PR #61 remains separately gated by its own native runner; this module does not alter or claim that PR.

## Testing

Rust tests construct a versioned project with a 120-frame, two-segment synthetic MLT recipe, real temporary FFV1 **test bytes** and a real 4-second 48 kHz stereo RIFF/WAVE **test file**. Tests verify stable source fingerprint, exact rational alignment including fractional FPS, refusal of mismatched durations, foreign revisions, reorders, source hash or size tampering, missing voice binding, unsupported codecs/caption claims, fabricated provider state and symlink substitution. These synthetic fixtures validate the **input-checking code**; they do **not** prove FFV1 decoding, video assembly or end-user playback.

No claim is made that any of the 60 independent product acceptance scenarios has passed.
