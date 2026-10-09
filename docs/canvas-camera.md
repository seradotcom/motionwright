# Semantic Canvas camera projection

Motionwright's Canvas now visually projects its **stored per-scene camera** onto semantic objects instead of showing objects at unadjusted project coordinates when camera parameters change. This closes an editor honesty and usability gap: the camera data was already versioned as `center_x`, `center_y`, `zoom`, `rotation_deg` and `safe_margin`, but the prior stage did not apply the first four parameters to object display.

The semantic viewport is exactly **1920 × 1080 project pixels**, and its display may be scaled to fit the actual screen. The camera position and zoom are properties of the scene, preserved through the app-owned creative transaction and SQLite revision model. A camera centered at (960, 540), zoom 1 and rotation 0 is the identity camera. Other camera settings are projected through a parent layer, so object positions, text scale, z-order and rotation are transformed together, not independently faked with a screenshot.

## Coordinate contract

For authored world point \((x,y)\), camera center \((c_x,c_y)\), zoom \(z\) and camera rotation \(\theta\) in degrees, the editorial viewport represents

\[
x_s = 960 + z[\cos\theta(x-c_x)+\sin\theta(y-c_y)]
\]
\[
y_s = 540 + z[-\sin\theta(x-c_x)+\cos\theta(y-c_y)].
\]

The layer's CSS transform composes viewport-center translation, inverse camera rotation, zoom and translation by camera world center. It uses an origin at the top-left of the stage and never modifies `CanvasNode`'s stored world coordinates or keyframes. The safe-frame overlay stays locked to the **output viewport**, not the zoomed world, so safe margins retain their editorial meaning.

A pointer drag in screen-space is transformed by the **inverse** camera rotation and zoom before applying it to the original object world position. This holds for base-pose dragging and for the explicit Auto-key X/Y gesture. Without this inverse, a 2× zoom would author 2× too much motion and a rotated camera would edit the wrong axis. The camera transform itself does not unlock any scene/object property and cannot bypass the same semantic edit policy.

Canvas now exposes camera **Center X**, **Center Y** and **Rotation** inputs alongside its existing zoom and safe margin controls, using the same canonical `set_camera` revisioned change. Numeric limits and nonfinite checks remain authoritative in Rust; invalid inputs are not silently interpreted as completed camera operations.

## Boundaries

This is a *semantic editorial view* and is always labeled as such. It does not imply that the currently selected renderer supports the camera transform, that a cached or rendered frame exists, that fonts/fill/stroke match a native output, or that a multi-renderer export can preserve everything visually. Canonical Film and Blender/Manim adapters continue to reject any unsupported camera parameter rather than flattening an authored composition. Camera editing does not create a media production job, read private disk files or alter the source Semwright Native SDK pin.

## Verification

- `canvasCamera.test.ts` checks identity framing, zoom/pan/rotation projection, exact inverse pointer delta, fixed safe margins, nonfinite and zero-dimension refusal.
- `canvas-camera.spec.ts` checks real browser camera controls, visible stage framing, fixed safe area, world-space drag behavior under a rotated zoom camera, no unexpected keyframe commits, and shared revision behavior.
- Existing independent native Film tests retain authoritative fail-closed support checks. Actual rendered camera parity requires separate, independently reviewed output evidence and is **not** claimed by a CSS projection.
