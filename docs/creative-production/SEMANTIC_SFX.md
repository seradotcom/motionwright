# MW05-E08-03 · Source-bound semantic sound choreography

Status: PARTIAL IMPLEMENTATION. This is a bounded original stereo PCM
authoring and preparation workflow, not a fully mastered H.264/AAC
deliverable, licensed sound library, or human creative approval.

## Authoring and authority

Audio → Semantic SFX choreography uses versioned cue IDs and explicit
event types: scene_reveal, beat_emphasis, ui_focus, confirmation, impact,
transition. Every cue attaches to an existing scene and an explicitly
authored scene-start or beat-start anchor. It references a previously
imported WAV asset by its exact lowercase 64-character SHA-256, source
frame duration and original source-rights declaration. The editor exposes
a bounded signed offset, milli-dB attenuation, and attack/release envelope.

Changes UpsertSemanticSfx and RemoveSemanticSfx use the existing
StudioService durable project journal, generation/revision CAS and Native
SDK operations audio.sfx.upsert and audio.sfx.remove. A cue is not an
external file path, source execution request, alleged authentic recording,
or completed mixer artifact.

An attached cue prevents source-asset deletion. Edits mark only their
own scene as draft without erasing human Canvas, camera, unrelated scenes
or deliverables. Domain and Studio validate 1–180 source frames, -15..30
frame offset, -36..0 dB source gain, 0–30 frame attack, 0–90 frame
release, strictly positive sustain, and no more than four concurrent
cues or 64 cues per project. Anchors and scene/cue tails outside the
authored scene, stale beats, invalid sources, unsafe schema extensions
and unsupported time bases fail closed.

## Actual native audio bytes

The owner-only StudioService verified_semantic_sfx_wav boundary checks
project identity, revision, attached asset ID and source hash, then reads
the actual immutable bytes from the original blob store. The native SFX
PCM preparation verifies the original SHA and independently parses one
exact stereo PCM16 WAV data chunk at 48 kHz. Canonical 30 fps source
frames map to 1,600 PCM frames each; no silent resampling, time stretching
or implicit source trimming occurs.

The native mixer applies each authored gain, attack and release curve and
combines up to four source effects. It outputs a real stereo PCM16 WAV and
returns measured sample-peak PCM16, duration and source hashes. An
oversummed sample fails instead of silently invoking a limiter or claiming
LUFS/true-peak compliance. Version 1 is explicitly limited to complete
30 fps / 48 kHz film output no longer than 900 video frames (30 seconds).

## Independent acceptance

Rust domain and TypeScript tests check exact source/beat positions,
revisions, owners, failed source deletion, envelope/overlap bounds and
legacy serialization. SQLite tests import actual original WAV files
and prove strict owner/revision access, immutable content and idempotent
request replay. Native tests inspect actual decoded source and mixed
stereo samples, ensure correct onset/release, reject forged RIFF/SHA,
non-30-fps profiles and clipping.

A separate CI job produces two distinguishable original synthetic WAV
stems and runs the Native source mixer through a real StudioService
project. Python independently decodes the output PCM and cross-checks
24 source/mix sample values at silent, attack, overlap, tail and post-tail
positions. The result is uploaded as actual WAV evidence, not a fabricated
JSON-only claim. The workflow does not substitute silent or generated
acceptance media for input sources.

Remaining work: project source mix into the existing pinned Semwright MLT
AV mux and separately decode the H.264/AAC deliverable; optional imported
music/voice score, user-reviewed cue energy and time series, real measured
LUFS/true-peak, source rights and human creative listening approval.
MW05-E08-04 remains separate and uncompleted. This partial slice requires
green CI at the exact pushed commit before technical acceptance.
