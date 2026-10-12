# MW05-E08-01 — exact multi-scene conform to an author-marked music grid

This extends the existing editable musical clock without a new renderer,
DAW or independent backend. It is a bounded editorial operation:
2–16 unique scene endings may be aligned to selected beats in one saved,
content-addressed, owner-authored production music plan.

## Editor workflow

Production → Production plan → Musical edit clock →
**Conform multiple cuts to the music grid**. Select at least two
scene boundaries in original scene order and assign an exact saved
beat to each one. Studio displays all affected scenes, changed start
and duration and total project duration.

The preview is read-only. The user must save the music grid before
conforming, and explicitly acknowledge when changed scene boundaries
could affect global audio cues, markers or transcript segments.

The edit type is conform_scenes_to_music with a bounded array of
scene_id and beat_index, exact current base_revision, and content
SHA-256 of the owner-selected audio. The trusted Rust domain re-evaluates
the entire plan. The Semwright Native SDK descriptor is
scene.music.cuts.conform, handled by the same StudioService
project-CAS transaction as the original single-cut operation.

## Atomicity and edit authority

The trusted preflight rejects all of the following before any write:

- nonexistent, duplicate, out-of-order or excessive boundary targets;
- missing audio, incorrect digest, absent saved music clock, invalid beat,
  nonpositive scene duration or rational overflow;
- scene-local authored beats or keyframes that would be cut off;
- timing locks on the project or any scene whose start or duration
  changes, including all downstream scenes;
- delivery profiles protecting any affected scene timing;
- stale revision or unacknowledged impacts on absolute audio anchors.

A valid sequence is staged on a clone, validated as a whole and
committed through one revision. Changed scenes return to Draft and the
earlier concept approval is cleared. The original canvas IDs,
object keyframes, music bytes, global cues, markers and transcript
times remain unchanged. No automatic audio remix, resampling, beat
detection or native render takes place.

Unlike editing each scene independently, applying multiple cuts does
not publish an intermediate, partly shifted timeline. An invalid
second boundary leaves the entire original project unchanged.

## Verification and limitations

The existing music-clock workflow now runs domain tests for precise
rational cuts, duplicate/unsorted targets, keyframe bounds, protected
downstream scenes, digest identity and anchor disclosure. SQLite tests
admit a real PCM16 WAV through the canonical CAS service and verify
one revision, unchanged source bytes, stale CAS and reopened identity.
Native SDK tests reject malformed operations. Studio and Chromium
tests exercise the complete review-and-commit workflow.

NOT established: audio onset measurement, beat/voice sync quality,
arbitrary waveform-aware retiming, final AAC/H.264 sync, human review,
rights to use the soundtrack, or automated musical edit quality.
MW05-E08-01 stays partial and the 60 independent acceptance scenarios
remain NOT_RUN.
