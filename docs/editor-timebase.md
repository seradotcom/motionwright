# Profile-based editorial timecode

The Motionwright editor uses one project-global editorial playhead. Its displayed frame count and frame stepping derive from the selected **versioned delivery profile's exact rational frame rate** (numerator/denominator). The first profile is selected when a project opens; the user can change the preview timebase without creating a project revision.

**Timebase and timecode are display settings, not source or export authority.** Native Semwright remains responsible for exact renderer frame ranges, media sampling, effects and delivery. The app-owned project continues to store rational presentation times; the TypeScript editor converts to floating seconds only to position the interactive playhead. An invalid/missing profile has a visibly identified 24 fps fallback rather than a silent arbitrary assumed rate.

### Numbering

- Integer rates (24/25/30/60 fps) use nondrop-frame timecode. Noninteger rates such as 24000/1001 also default to NDF.
- At exactly 30000/1001 or 60000/1001, a reviewer may **explicitly** select drop-frame (DF) numbering. DF skips two or four frame *labels*, never source video frames. A semicolon marks DF versus NDF's colon.
- Frame stepping uses one exact rate-derived frame interval; display quantization floors to the preceding frame boundary so a pre-cut timestamp never appears as the first frame *after* the cut.
- The ruler shows elapsed presentation time, independent of frame numbering mode. Captions, measured audio sample clocks and original media time are not silently retimed by changing timebase.
- Timecode does not certify that the native renderer produced a frame or that an output video is CFR, correctly synced or approved.

The Studio's pure timecode tests cover NTSC fractional-rate SMPTE landmarks at 1798, 1800, 17982 and 107892 frames, integer rates, invalid profiles and frame-step bounds. Browser regression covers selection of 25 fps and 30000/1001 profiles, explicit DF/NDF switching, shared Canvas time and the invariant that changing a display setting leaves the creative project revision intact.

Native end-to-end acceptance and master render A/V sync stay separate gates; a green UI test is not evidence of those operations.
