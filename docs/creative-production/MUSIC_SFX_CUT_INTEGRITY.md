# MW05-E08-01 x MW05-E08-03: semantic SFX tail guards on musical cuts

## Timing contract

A music cut is a revisioned editorial request to retime scene boundaries.
Semantic SFX attach to authored scene/beat anchors, whereas ordinary audio
cues, markers and transcripts retain absolute times. Neither authority may
be silently conflated.

Previously the single/multi-cut inspector checked keyframes and authored beats,
but did not preflight the resulting SFX schedule. A cut could appear actionable
even though applying it would truncate a scene-bound sound or release tail.
The Studio inspector missed the same check. The multi-cut transaction already
validated a staged project, but that occurred too late for truthful previews.

## Implemented behavior

- Both single and 2-16-boundary inspectors stage a temporary copy of scene
  timing, then call the existing semantic SFX scheduler. They reject truncated
  sources/tails and invalid overlap or bounded schedule.
- No waveform inference, source resampling, unrequested crossfade, source
  trimming, or changes to immutable SFX asset bytes are introduced.
  At 30 fps, 48 kHz schedules retain exactly 1,600 samples per frame.
- Reviews separately count scene-linked SFX whose scene start or duration
  changed; downstream motion is included.
- Acknowledgement is required if either global fixed-time anchors or linked
  SFX are impacted. This does not count as human listening acceptance.
- Single-cut mutation now stages reflow and whole-project validation on a
  clone before publication. The multi-cut already uses this atomic technique.
- Actual revisions and generation CAS remain in the existing
  StudioService / Semwright Native SDK service path.

Studio mirrors these constraints for responsive demonstration, while Rust
re-evaluates the request at mutation time. Preview is not edit authority.

## Regression gates

The existing music-clock GitHub Actions workflow checks Rust domain,
SQLite service, Native SDK, Studio unit/build and Chromium acceptance.
New contract cases cover:

1. Reject a shortening across a six-second SFX beginning one second into
   a seven-second scene, without any partial edits, for single and batch cuts.
2. Shift a downstream scene with scene-linked SFX, retaining its immutable
   source identity and envelope while updating exact source sample positions.
3. Show the number of affected scene-linked SFX separately from absolute cues.
4. Require review acknowledgement for semantic SFX impacts.
5. Reject a stale local preview after a new SFX cue has been added.
6. Keep original project identifiers, clock source bytes and CAS semantics.

CI success must be checked for the exact PR merge SHA; implementation alone
is not production certification.

## Remaining gaps

No automated onset measurement, proven absence of clicks, R128 mastering,
verified H.264/AAC audio sync, rights clearance, or human creative sign-off.
MW05-E08-01 and MW05-E08-03 remain partial.
