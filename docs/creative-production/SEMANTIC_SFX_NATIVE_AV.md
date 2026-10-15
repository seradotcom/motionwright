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


## Visual Film parity before native dispatch

A read-only, GitHub-CI-only original-source preflight loads the same owner
SQLite revision after the two imported SFX assets and cues are committed.
It builds one real 60-frame Motion Canvas Film and compares its serialized
visual bytes against an otherwise identical visual-only clone, with no
native provider running or mutating. Audio assets must remain outside
the visual Film asset registry; every authored shot, temporal constraint and
object identifier must be identical. The preflight has a scoped SHA-256
record and is not a native render success or finished audiovisual evidence.


## Bounded typed native errors (SFX fixture only)

The previous native error was downgraded to
`malformed_native_failure_response` because the native test binary wrote
a full private JSON diagnostic followed by a Rust error line. This prevented
the safe CI classifier from learning even the SDK's closed error code.

The owner-local full error/receipts remain in a private file. For this SFX
acceptance fixture alone, the binary now additionally emits a *closed typed
error envelope* containing only an allowlisted SDK code, the SDK's
outcome-known flag, and an allowlisted list of operation kinds and stages
(with deliberately empty receipt payloads). Identifiers, native job references,
paths, untrusted provider messages and raw stderr are never included.
The existing secure failure classifier handles that limited envelope and
prints a bounded count of acknowledged render starts.

The render and master errors have distinct diagnostic phase values. A typed
failure is **not** a verified negative or positive native execution outcome
unless the pinned SDK says the outcome is known. No failed native render is
automatically dispatched again, and none of the new diagnostics can promote
a WAV or MP4 into a public PASS result.


## SDK-specific failure classification and receipt bounds

The 62648bb acceptance run confirmed that the SDK returned a known negative
result at Motion Canvas render, but the shared safe classifier grouped the
native error enum as OtherOrMissing. This is NOT enough evidence to claim a
provider crash or a defect in the SFX mixer.

For the synthetic SFX acceptance fixture only, a separate source-private
record now contains strictly validated SDK error categories (e.g. BackendFailed,
InvalidArgument or Unsupported), closed source-defined reason classes and
bounded non-identifying counts of native plan/apply/render/verify/mux receipts.
No provider messages, file paths, session IDs, operation IDs, raw receipts or
unrecognized strings are included. The companion CI reader validates the
exact schema, rejects duplicate keys and refuses any successful-media claim.
An absent or untrusted record is printed as NOT_VERIFIED, never guessed.

Receipt counts show what the owner journal observed; a missing recorded
acknowledgment is not proof that an already-dispatched native operation never
occurred. No retry or release is enabled by this diagnostic.

## Exact pinned renderer failure class

The previous (56cbeb7) source-only render CI confirmed a terminal
render.status=failed after one acknowledged render.start, six status
observations and no native mux. Baseline native AV also passed its own
independent workflow. That evidence does not establish whether the cause is
renderer-specific or transitory. The new diagnostic extracts ONLY the
allowlisted RenderFailureClass already present in the existing Motionwright
coordinator's sanitized error, validates parity with its canonical Rust enum
list, and refuses foreign/unknown text. No raw provider error, local path,
job reference or environment variables enter public logs. Native render failure
remains a failed native run, never an audiovisual PASS.


## Production-use original SFX PCM preparation

The native service can now prepare a source-bound WAV via the public
ProductionCoordinator.prepare_native_semantic_sfx_av_audio method rather than
requiring the separate CI-only semantic-sfx-av-prepare example. The underlying
prepare_semantic_sfx_av_audio function reads original imported WAVs from
StudioService using the current project generation/revision stamp, calls the
existing source-exact native semantic SFX mixer, and writes a private 48 kHz,
stereo PCM16 WAV to the authorized owner output directory.

The filename is derived only from the project and generation UUIDs, revision,
bounded frame count, and mixer SHA-256. It cannot be chosen by a browser or
untrusted provider. Newly created files use exclusive creation and (on Unix)
owner-only permissions; existing digest-named sources are reverified and never
overwritten. A read-after-write full-byte comparison, owner root security
checks and renewed project CAS check must pass before the method returns a
PreparedSemanticSfxWav containing the verified MltAudioArtifact and source
admission proof. Failed/stale creation never dispatches a native render.

The prepared WAV may then be used with the existing
assemble_native_semantic_sfx_av_master routine; the latter independently
regenerates the authorized PCM before the pinned Semwright Native SDK's
MLT AV mux. This closes the core native preparation-to-admission boundary.
The actual Studio UI/Plugin export action, user approvals, optional voice/
music stems and native MP4 creative acceptance are still separate gaps.


## Native end-to-end path uses the production preparation API

The source-bound SFX audiovisual fixture now invokes the SAME
prepare_semantic_sfx_av_audio routine after importing the two original WAVs
and authoring their semantic cues through owner-local StudioService CAS.
The previous numeric original-source mixer remains an independent control:
its entire PCM output must match the bytes of the prepared digest-named WAV,
but it is **not** substituted for the production WAV at native mux time.

The MLT request uses the verified digest-named private WAV. A separate
immutable copy under semantic-sfx-master.wav exists only for synthetic
human/independent decoder evidence. The evidence verifier and curated
artifact admission check these bytes are identical, and fail closed if the
preparation flag, revision, source count, digest, filename or PCM differs.
This is a real native output preparation path, but the target Motion Canvas
provider still needs separate AV acceptance and creative sign-off.
