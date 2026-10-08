# One editorial clock across workspaces

The project-global playhead in `App.tsx` is the single source of **editorial time** for Timeline, Storyboard, Canvas and Audio. Seeking does not mutate the creative project or increment its opaque revision. [Display timecode and frame stepping](editor-timebase.md) follow an explicitly selected delivery profile's rational frame rate; this selection does not change media or export metadata.

## Time domains

- The timeline, transcript and audio cues use absolute project seconds.
- Canvas keyframe timestamps are scene-relative. Canvas computes `local = clamp(global - scene.start, 0, scene.duration - 0.001)` and converts seeks back with `global = scene.start + local`.
- A seek selects the scene that contains the new absolute time and pauses editorial transport. At the exact end of the project there may be no containing scene; the existing selection remains.
- A scene selection in the project rail seeks to that scene's start. A keyframe jump seeks to its absolute place in the project; it never creates a second playhead.

## Evidence and scope

The editor's playhead is a **design representation**, not a guarantee that an audio sample or renderer frame has been decoded. The Audio editor distinguishes measured waveform pages from editorial time. Cue jumps refer to explicit time records, not automatic transcript alignment. Neither scrubbing nor a cue/keyframe jump writes project state.

Native SDK and Semwright remain the authorities for renderer operations and artifact evidence. Changing an animated keyframe is a separate transactional edit using the same project CAS boundary as other creative edits.

## Regression

`apps/studio/tests/editor.spec.ts` checks cue → selected scene → Canvas time, reverse cue navigation, frame navigation and that seeking preserves project revision. It checks a real authored keyframe afterward. Browser fixtures are synthetic; this is UI contract evidence, **not** native video/audio playback or independent product acceptance.
