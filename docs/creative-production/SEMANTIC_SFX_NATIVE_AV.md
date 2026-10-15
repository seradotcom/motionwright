# MW05-E08-03 + E08-04: original semantic SFX to native H.264/AAC

Status: bounded E2E implementation. Creative approval and release are NOT implied.

## Source and edit authority

The existing semantic SFX project CAS, owner-imported WAV assets and native PCM
mixer are retained. This workflow takes the mixed audio into the existing
Motionwright ProductionCoordinator and the pinned Semwright Native SDK/MLT
Driver Host for actual MP4 output. No independent renderer, direct-ffmpeg
production backend, scheduler, or parallel project store has been added.

1. Create one original 60-frame 1920x1080 / 30-fps project in SQLite.
2. Import two original 48kHz stereo PCM16 source WAVs through StudioService CAS.
3. Author scene-linked cues at frames 6 and 20 with 24-frame source lengths:
   first source L440/R660Hz, second source L740/R980Hz.
4. Verify imported WAV bytes against the project revision/source digests and
   generate an exact 96,000-frame stereo mix via the existing Rust native mixer.
5. Render the SAME source project through pinned Semwright Motion Canvas.
6. Produce an H.264/AAC MP4 via the pinned MLT AV mux with the exact
   SHA-256-bound SFX WAV as input.
7. Independently decode the actual AAC/MP4 in CI (never in the product):
   verify streams, frame count, pixel movement, source tone identity,
   onset, overlap, exclusive-event windows and release-tail silence.
8. Independently reconstruct the original PCM from the two admitted WAVs.
   Require equality to within one signed PCM LSB of the native mixed output.
9. Release only curated synthetic WAV, MP4 and frame evidence upon all gates
   passing for the exact commit and source project identity. Do not publish
   Broker logs, private command payloads or owner paths.

The bounded decode requires first audible onset within one AAC packet
(1,024 audio samples) of the authored 0.200-second source cue. Exact media
timing here is a TECHNICAL fixture, not an auditory-quality judgment.

## Execution

Workflow: .github/workflows/semantic-sfx-native-av.yml
CI-only fixture: MOTIONWRIGHT_E2E_FIXTURE=semantic-sfx-av
Owner-original source helper: crates/native/examples/semantic-sfx-av-prepare.rs
Independent decoder proof: tooling/semantic_sfx_av_evidence.py
Curated artifact policy: tooling/semantic_sfx_av_public_artifact.py

The existing baseline AV fixture remains unchanged unless this explicit
CI-only fixture is selected. It must not be counted as the new SFX proof.

## Limits

These are original NUMERIC synthetic tones. No external rights clearance,
real customer product audio, LUFS mastering, true-peak guarantee, absence of
codec clicks, narration/music ducking, arbitrary-length mix, human creative
listening sign-off, or production release is established.

MW05-E08-03 and MW05-E08-04 remain PARTIAL until these wider requirements
are demonstrated with independent acceptance evidence.

## ProductionCoordinator source admission (additional implementation)

The same owner-CAS audio source can now be admitted through the dedicated
ProductionCoordinator semantic SFX wrapper, which rebuilds the exact native
PCM mix from current imported source blobs, refuses stale revision/generation,
wrong deliverable, alternate mix bytes or substituted owner output file, and
ONLY THEN calls the existing MLT AV mux. The CI render fixture exercises the
wrapper rather than relying solely on the declared SHA of a supplied WAV.

This is a native lower-level contract. It does not yet add a standalone
user-facing Studio export action or generate the PCM artifact for the caller.
It also does not claim that arbitrary 3D or multi-scene sources have a
compatible semantic audio mix. The exact 60-frame bounded scenario remains
the evidence limit. Rust tests exercise real SQLite CAS source admission,
wrong revision/deliverable, modified output bytes, missing audio and symlinks.


## Safe native render phase diagnosis

The previously tested SFX E2E failed at the Motion Canvas render-command
boundary, **before the MP4 mux**. Its only public failure signal was
malformed_native_failure_response; that does not prove a provider crash,
an audio source problem or a successful/failed native mutation.

For this explicit synthetic SFX CI fixture alone, native-render-e2e records
one closed-enum phase marker next to the PRIVATE render evidence: entered,
owner_project_loaded, fixture_checked, owner_connection_ready,
native_request_entered, native_request_returned, native_result_checked or
motion_evidence_written. The CI failure wrapper prints only that allowlisted
phase or NOT_VERIFIED. No owner path, session, job ref, raw command output,
provider message or receipt can be copied to a public artifact. An entered
native request is NOT proof it was acknowledged; UNKNOWN remains unresolved.
The original render is not retried automatically.

The stage validator rejects unknown, duplicate, oversized and non-authoritative
success-marked input. It is diagnostic visibility, not creative acceptance or
native evidence. The AV master is still approved technically only after
the actual source-bound WAV, pinned broker MLT mux and decoded AAC checks pass.
