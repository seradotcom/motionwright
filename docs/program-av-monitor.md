# Source-scoped native AV monitor in Timeline

Motionwright's Program preview can review an original Semwright MLT H.264/AAC master for one or several known Motion Canvas segments. It uses the same bounded, read-only original-media reader as Deliver: MP4s up to 1 GiB are served through ranges of at most 4 MiB, with owner, revision and integrity checks described in [native AV review](native-av-review.md).

## Matching requirements

**Review final AV** is available only for a current, source-bound result:

- The master has a nonempty session token returned by the trusted Tauri production path.
- The master, source render and selected output profile agree on project, generation, revision and profile. The visual rate must equal the saved profile's rational rate.
- A single-segment master matches that segment and its frame count. A multisegment master matches the ordered segment count and total frames; every segment has a distinct identity, known source scenes and a consistent duration.
- All represented scenes are unique, retain their saved order and use Motion Canvas. An explicit profile cut cannot gain an omitted scene. Unrelated renderers and missing or stale sources remain in their other representations.
- Scene durations agree with the original frame clock to one source frame. The monitor does not retime media or invent padding.
- Multisegment results retain their exact H.264/AAC, 48 kHz stereo contract, original digest and review-only classification. Captioned originals without device-local glyph verification remain clearly labeled **review only**.

The original multisegment result lives in the application session, so navigating from Deliver to Timeline and back retains its source and delivery eligibility. A new project open clears that session result. Export still requires a fresh explicit DeliverLocal permission, and an edited revision cannot reuse the previous result as current.

## One media clock across scenes

The Film cut reflows its selected scenes onto a zero-based output clock. Program sums the saved durations before the current scene and adds its authored scene-relative playhead. This also maps a multisegment master and a cut that omits an interval from the original project.

Playing the original MP4 updates the shared editorial playhead and scene selection. Crossing into the next represented scene keeps the same video element and original read; it does not open another master or restart the decoder. An explicit editorial seek pauses playback and moves to the corresponding media position. Starting editorial preview pauses the decoder so two clocks cannot advance independently.

At the end of the included cut, the mapped playhead stays inside the final represented scene. An unrelated scene shows its own representation. Returning to a represented scene can reuse the existing opt-in only while the same original master, profile and revision remain current. A different profile, revision or master consumes that choice and requires another explicit review.

The WebView determines seek precision. Frame-perfect seeking, sample correlation, caption glyph quality and human audiovisual approval remain separate installed-device checks. The original file retains its native source identity throughout review.

## Focused verification

The programMaster.test.ts suite covers single and multiple source segments, original identity, per-segment and aggregate clocks, duplicate or shuffled sources, omitted intervals, changed profiles and inconsistent caption review flags.

The native-multisegment-av.spec.ts suite uses the real App workspace transitions with an explicitly synthetic Tauri bridge. It verifies that navigation preserves the original master, export requests use a fresh permission, captioned results remain restricted and edits make the old result historical.

The program-multisegment-playback.spec.ts case plays an existing synthetic H.264/AAC clip through the actual Program component. It checks continuous decoding across a segment boundary, one original read, editorial seeking and cleanup after a revision change. This tests the component's media-clock behavior; it does not claim a Native render or installed-package acceptance.

The single-segment browser contract remains covered by program-av-monitor.spec.ts. Actual Semwright Motion Canvas and MLT production evidence retains its own exact-source scope. No renderer, grant, publication or G2/G3 authority is created by these UI checks.
