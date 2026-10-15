# Motionwright v0.5 E08-02 — recorded narration source and conservative replacement review

**Technical partial.** Adapted from the earlier isolated drafts
[#94](https://github.com/seradotcom/motionwright/pull/94) and
[#95](https://github.com/seradotcom/motionwright/pull/95) into the current
`Project`, `ProductionDesign`, `StudioService` and pinned Semwright Native
SDK. These old draft branches remain untouched.

The integration does **not** install the old parallel creative-library
service, create a new audio engine, run ASR, execute a voice provider or
silently recut a project.

## Original source protection

- A user explicitly selects an **active imported measured audio asset** with
  exact SHA-256 identity, 1–5000 transcript segments and source-linked cues.
  A source digest contains the voice-track metadata, imported asset digest,
  retained words, exact rational timing and cues, independent of array order.
- Only manually aligned or measured-confidence >=0.800 word timings can be
  recorded as a protected original narration source. Unknown ASR alignment
  or unknown cue time remains inadmissible.
- A separate reviewer declaration, reason and explicit UI confirmation
  precede `record_narration_take`. The canonical project edit goes through
  existing SQLite revision CAS and the pinned Semwright Native SDK operation
  `driver.motionwright.narration.take.record`. Neither the reviewer string
  nor the source digest is a signature, publication right, or acoustic proof.
- While protected, replacing the active voice, transcript words/timing or
  source-linked cue, plus checking out/merging a creative branch, is refused.
  Existing music/mix edits remain possible. Directly tampering with serialized
  audio after taking the lock fails `Project.validate`.
- The owner must explicitly request `release_narration_take` with the
  **exact recorded content SHA**, reviewer label and reason. This uses the
  existing durable request-key CAS, never a new permission engine.
- The source lock is optional in the existing format-2
  `ProductionDesign` document, so earlier valid projects read without
  new data loss. Format-1 writers cannot silently discard protected creative
  production fields.

## Conservative edit-impact inspection

**Audio → Narration take** displays the current read-only source snapshot.
A baseline can be retained while the same project advances to a later
revision. The user may then request a *read-only* comparison:

- Changed transcript segments, moved exact rational timestamps and
  linked cues are identified without manufacturing a new ASR transcript.
- Only proven local scene overlaps are called local overlaps.
  **Every** scene is conservatively marked for cuts and B-roll review,
  because indirect pacing, transitions and caption dependencies may extend
  beyond the edited words.
- Caption-enabled output profiles are explicitly listed for another
  editorial review; changed voice media requires remeasuring the mix.
- No result claims that captions, audio, B-roll, camera, transitions or
  rendered masters were regenerated. No replacement is automatically
  approved or committed by a comparison.

Tauri's read-only commands `native_narration_take_snapshot` and
`native_narration_replacement_impact` check project ID, generation and
revision before and after computing. Studio rejects source mismatches,
missing critical fields and false authenticated/committed flags. Responses
from older async requests are dropped on revision/project changes.

## Validation and remaining work

Adversarial Rust tests cover measured timing, exact asset digests,
unchanged mix authority, branch and source lock bypasses, malformed JSON,
portable snapshots and SQLite reopen/CAS; domain impact tests cover
word/cue changes, source drift and conservative scene review. Native SDK
tests ensure mutations require observed-resource authority. Studio tests
check the browser demo cannot forge approvals; GitHub CI runs the heavy
Rust, native, service, Studio build and Chromium suites on disposable runners.

This pass does **not** implement automatic recaptioning, voiced cut
conformance, sample-accurate master export, real sound mastering, signed
voice rights, independent human speaker consent, adjudicated creative
acceptance or multi-user identity proof. E08-02 remains **partial**,
all 60 human acceptance scenarios remain NOT_RUN and `release=false`.
