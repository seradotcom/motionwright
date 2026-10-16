# Motionwright v0.5 — original three-bus native PCM audition

**Requirement:** MW05-E08-04 — **partial, physically rendered audio**.

This integrates the already proven original mixer of draft
[PR #87](https://github.com/seradotcom/motionwright/pull/87) and
the source-custody/local-audition work from draft
[PR #88](https://github.com/seradotcom/motionwright/pull/88).
Their branches remain untouched. We adapted the DSP to the existing
`motionwright-native` Rust crate, which is already the project's pinned
Semwright Native SDK consumer, and reused **StudioService**, verified blob
storage, audio import and the current **Audio Workspace**. No duplicate
project/backend/scheduler or new live provider was introduced.

## An actual local sound-production workflow

Inside **Audio → Voice / music / SFX · original source mix**:

1. Import three **different source-owned audio/WAV assets** to the current
   project. The voice stem must be the active measured 48-kHz **stereo**
   track. Music and SFX are chosen explicitly from current imported
   content-addressed assets, not from arbitrary filesystem paths.
2. Choose the existing 48-kHz deliverable language/profile (en, es, de).
   Voice/music bus gains come from the **saved** `Project.audio.mix`,
   not from another editable timeline.
3. Choose bounded SFX gain, music attenuation, attack/release samples and
   explicitly attest rights for **local audition only**. Owner statement
   is not independently verified.
4. Source-linked, manually timed/high-confidence voice word intervals
   provide exactly the duck windows. No unknown ASR-derived timestamp,
   generated speech activity, implicit resampling or new audio provider.
5. The existing StudioService resolves each current-project asset through
   the **verified immutable SHA-256 blob store**, re-hashes the original
   file while decoding it to 48-kHz stereo signed 16-bit PCM, then checks
   the project revision again. Three container hashes and three decoded
   PCM hashes are distinct and explicit.
6. The bounded native Rust mixer composes no more than **15 seconds**
   of source PCM with deterministic sample-accurate ducking. It refuses
   clipping or violation of the −1 dBFS sample-peak guard rather than
   silently inserting a limiter or normalization. No project `Change`
   is committed. The desktop returns only a bounded **WAV audition**,
   not a published master. Studio displays a revocable, in-memory audio
   player that is discarded on any project, source selection, rights
   attestation or mix-parameter change.

The read-only desktop Tauri command delegates to the existing source
service and `motionwright-native::original_mix`. It is not an independently
granted Semwright operation or a new audio runtime; Semwright still
governs canonical project mutations and native rendered masters elsewhere.

## Exact evidence, not a creative PASS

Native unit tests assert SHA-256 source identity, exact measured windows,
no clipping, denial of unreviewed ASR, locale/voice mismatch, reproducible
PCM and strict refusal of fabricated mastering/approval flags. Separate
StudioService tests use actual imported content-addressed WAVs and check
portable source custody, foreign-project references, stale revisions,
sample-rate mismatch and symlinks.

A disposable Linux GitHub Actions test uses three sets of **synthetic,
non-speech tones** (en/es/de). The fixture writes WAV files and a
non-authoritative technical receipt. Independent FFmpeg decode must
produce *byte-identical* 48-kHz stereo PCM; FFmpeg `ebur128=peak=true`
reports the measured integrated loudness and intersample true peak,
and Python verifies music ducking against the original source PCM.
These quantitative measurements concern only this synthetic fixture,
not commercial speech or music and not the final MLT/AAC master.

A separate Chromium suite covers the browser demo's **no native WAV**
behavior, a synthetic desktop transport requiring explicit rights
attestation, no canonical project revision, and an in-memory player
that is marked unmastered. Its synthetic transport is not real-native
audio evidence. Real audio verification is owned by the Rust/FFmpeg CI.

**Outstanding:** full physical **Semwright MLT H.264/AAC** mixed-master
injection, true EBU R128 measurement on real owner media, all language
profiles and arbitrary music timing, source rights/speaker consent,
independently verified human listening/intelligibility, sample-accurate
AV sync against actual motion, 60 private creative acceptance cases
(NOT_RUN). Release authorization remains **false**. A technical PCM
preview is not a product master or publication approval.
