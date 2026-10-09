# Typed motion authoring

Motionwright stores motion authoring as application-owned semantic state rather than transient editor animation. The first bounded surface covers transform-like canvas properties while keeping the project revision, locks and Native SDK operations authoritative.

## Data model

Each `CanvasNode` may contain up to 128 typed `CanvasKeyframe` values. A keyframe contains:

- scene-local rational time;
- one property: `x`, `y`, `width`, `height`, `rotation_deg` or `opacity`;
- a finite numeric value validated for the selected property;
- one editorial interpolation: `hold`, `linear` or `ease_in_out`.

A node cannot contain two keyframes for the same property at the same rational time. Keyframes are sorted deterministically by time and property after mutation.

Time is a half-open scene-local interval. A keyframe must satisfy `0 <= at < scene.duration`. Reducing a scene duration fails if the edit would strand any authored keyframe.

## Editing and locking

Keyframes are changed through the same revisioned `Change` model used by the rest of Motionwright. The browser development fixture mirrors those operations, while the Rust service and SQLite transaction remain authoritative in the native application.

Property locks apply to authored motion as well as static transforms:

- `x` and `y` respect the position lock;
- `width` and `height` respect the size lock;
- `rotation_deg` respects the rotation lock;
- `opacity` respects the opacity lock.

Scene position locks also block keyframe edits. Native SDK mutations remain target-bound and use the same application transaction and revision CAS as other Motionwright operations.

## Studio preview

The Canvas workspace provides a scene-local playhead, typed property/value/interpolation controls, exact keyframe rows and explicit add/replace/remove actions.

The preview evaluates the stored keyframes for editorial feedback. It is not renderer evidence and does not claim pixel parity with a production backend. The [semantic camera projection](canvas-camera.md) applies authored pan, zoom and rotation to this editorial stage while keeping the safe-frame viewport anchored and pointer world deltas consistent. Static transforms remain the editable base state; moving the preview playhead does not bake interpolated values back into the project.

Canvas additionally provides an **explicit Auto-key X/Y** toggle, off by default. When on, a pointer drag at a scene-local playhead creates/replaces exactly **two position keyframes (X and Y) in one committed semantic change and one project revision**, capturing interpolation, time and selected object at gesture start. The base X/Y pose is unchanged. When off, dragging retains the existing base-pose transform behavior. Numeric **Commit transform** remains explicitly a base-state action in both modes, rather than silently converting size/rotation/opacity controls into new curves. The status label distinguishes BASE POSITION from KEYED POSITION; seeking itself never mutates project state.

Atomic position drag respects scene/node position locks, half-open rational scene bounds, existing keyframe replacement and the 128-key budget. Both axes are prevalidated before either is written; failed edits do not leave a half-keyed position. Pointer cancellation removes only local drag previews without creating creative changes.

## Native SDK

The public cooperation surface includes:

- `driver.motionwright.canvas.keyframe.set`
- `driver.motionwright.canvas.keyframe.remove`
- `driver.motionwright.canvas.position-keyframe.set` — one paired X/Y key at a scene-relative rational timestamp

All three commands use closed schemas and the same opaque Native SDK target binding as other mutating operations. The application never parses a Native SDK ref as a Motionwright resource identifier.

## Renderer boundary

Authored motion is intentionally fail-closed at renderer boundaries that do not yet have an exact mapping.

The native Semwright Film projection now admits the **exact paired linear 0→base X/Y settle subset**, with source/frame/safe-area checks and a real Motion Canvas E2E frame-difference gate. See [native linear position motion](native-linear-position-motion.md). All other Canvas keyframes remain explicitly rejected; Blender and other renderer adapters preserve their own independently verified boundaries.

Studio can author more motion than the current renderer supports, but preflight rejects any nonrepresentable curves. The admitted Film subset requires X/Y pairs at time 0 and at an exact frame-aligned positive time T, with the second pair equal to the base node X/Y; all four are linear. Multiple stops, Hold and smoothstep easing still fail closed rather than losing motion semantics.

## Verification status

Unit and contract tests cover deterministic replacement/order, scene bounds, property locks, duration protection, Native SDK schema exposure and fail-closed projection behavior. Heavy workspace, browser, desktop and Native SDK checks run in GitHub Actions.

This implementation evidence does not by itself convert the private product acceptance ledger to PASS; acceptance remains separately evidence-gated and independently reviewed.
