# Source-scoped native AV monitor in Timeline

Motionwright's Program preview can review the **real Semwright MLT H.264/AAC master** for a known native Motion Canvas segment without switching out of the creative timeline. This uses the same source-verified, read-only, at-most-16 MiB media capability available in Deliver; no general MP4 path import, new backend or direct decoder subprocess is introduced.

## Matching requirements

The Program's **Review final AV** control is shown only if all evidence identities match the current project:

- A nonempty ephemeral master export/read token was minted by the trusted owner Tauri production path in this session.
- The MLT master and canonical Motion Canvas source evidence have identical project ID, generation, creative revision, deliverable ID, segment ID, frame count, and exact rational frame rate.
- The selected scene is part of the exact ordered Motion Canvas segment. Other renderers, additional segments, missing scenes, altered deliverables or outdated project revisions are not eligible.
- The sum of included scene durations matches the verified native frame count / FPS to one source frame. The UI does not silently stretch video, invent padding, merge unrendered scenes or reinterpret the output profile.
- H.264/AAC MP4 is the explicitly certified master codec/container. All other profiles retain design/native PNG representations until the native output really exists.

The Program toolbar offers mutually exclusive **Design**, **native PNG frames** and **final AV** source modes. An AV view shows its verified source revision and is discarded automatically when scene/profile/source data no longer match. The originally authored scene and project stay unmodified when selecting a representation.

## Clock reconciliation

The canonical Film segment may contain an explicitly selected subset of project scenes. It reflows those scenes onto a **zero-based output clock**. Motionwright derives the corresponding source-media time by summing the versioned durations of selected scenes *before* the current scene, then adding the current authored scene-relative playhead.

Moving the shared editorial playhead seeks the decoded MP4 to the correct output time; it does not alter its source or create a new creative revision. Conversely, playing the local MP4's own controls sends decoded media time back through the selected scene map, updating the same global editorial playhead and scene selection. Native video play explicitly stops the synthetic editorial transport to avoid independent clocks advancing concurrently. If the editor transport is subsequently started, it pauses the native decoder and uses the editorial playhead as the current seek source.

The Video element can seek only as precisely as the specific WebView decoder permits. This is **not** a claim of sample-exact rendered playback, frame-perfect seek across WebViews, audio waveform sample-correlation, signed end-user media compatibility, or professional mix approval. The source files themselves remain SHA-256-verified immutable native artifacts; decoder behavior and creative quality require installed-device acceptance.

At the end of a selected cut, the mapped playhead is kept within the final included scene. Playback cannot accidentally jump into an unrelated next scene that the verified master did not render. Selection and seek never reinterpret a video from a different delivery profile as if it were from the chosen variant.

## Evidence

- Pure `programMaster.test.ts` verifies exact scene/segment/profile/generation/revision matching, rational-rate cuts, zero-based output time mapping, noncontiguous scene selections, the final-frame boundary, and failed-closed stale/forged inputs.
- Browser `program-av-monitor.spec.ts` uses an explicitly synthetic Tauri bridge to test gating before/after native mastering, read-only token-only IPC, toggling, variant change, renderer change, and project revision invalidation. It deliberately does not fabricate a playable, real H.264/AAC asset.
- Actual Semwright Motion Canvas and MLT AV master jobs are exercised by separate pinned-source CI lanes; installed-device decoder quality, true video/audio experience and the 60 independent product acceptance scenarios remain separate gates.

No change to the pinned Semwright Native SDK, runtime authority or authorization model is introduced.
