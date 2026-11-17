# MW05-E01-01 — exact cubic-out motion in the native Film

Motionwright v0.5 now admits one additional source-editable easing curve,
ease_out_cubic, for bounded native Motion Canvas X/Y movements and 0-to-1
opacity FadeIn. This progression is exactly 1-(1-t)^3, the same equation
already used by Studio previewTransform and the OutCubic easing in the
source-pinned Semwright Motion Authoring grammar (SOURCE_LOCK revision
04142a2ae16e53a58bb6cf43135786e396a4b720).

## Source editing, authoring and authority

In Canvas, choose an unparented Motion Canvas object, an existing
output profile, the starting X and Y, final exact output frame and the
Cubic ease-out option. The UI commits exactly ONE typed creative change:
set_canvas_out_cubic_position_motion. The application owns the Semwright
Native SDK operation canvas.motion.out-cubic-position.set. Four paired
keyframes are stored atomically in one project revision, with unchanged
source object ID, base X/Y, semantic structure and local styling.

The first X/Y keyframes are linear at zero time. Both endpoint X/Y
keyframes declare EaseOutCubic, bound to the saved profile's rational
frame rate. The older SetCanvasLinearPositionMotion semantics are
unchanged and backwards compatible. Authoring never overwrites
existing keyframes, scene beats, protected node positions, renderer
selection, or nested object hierarchies. The existing transactional
StudioService CAS and source authority remain the only mutation path.

## Lossless native Film translation

The Film adapter now emits a genuinely native MotionEasing::OutCubic
Invocation for one paired X/Y Settle and for an isolated 0-to-1 opacity
FadeIn. Rational duration, frame-aligned terminal keys, node identity
and Replan safe-area constraints are all preserved. Neither path
generates a raster substitute or a parallel renderer.

Studio's historical ease_in_out is smoothstep (3t^2 - 2t^3).
Upstream InOutCubic and InOutSine are different equations, so that
interpolation is NOT admitted or silently approximated. Mixed X/Y
curves, unframed endpoints and unsupported property channels also
continue to fail closed.

## Original real native E2E

The existing direct-vs-mediated Film conformance test retains the
original linear X/Y tile and linear opacity tile, adds one cubic-out
position tile and one cubic-out opacity tile and renders the same
two-second Film twice through the real Semwright Broker/Driver Host:
first mediated by Motionwright via Native SDK, second direct. Both
runs have distinct job identities, and all 60 decoded RGBA frames must
match exactly across runs.

The historical original 43310829074/1 moved X from 1410 to 1510 while
Y remained 750. Its original position label is retained, but those
pixels establish X motion only; they cannot establish nonconstant Y.

The current fixture starts at (1410, 620) and ends at (1510, 750).
Independent inspection uses one RGB24 crop per sampled position frame,
at (1370, 580) with dimensions 320 by 300. The solid source rectangle is
140 by 90 and RGB (119, 198, 250). The same original byte buffer supplies
both axis measurements, with pixel centers at column+0.5, row+0.5.
A dense solid-color mask must match the original box, both centroids
must follow 1-(1-f/30)^3, and all four edges must agree within 1.5 output
pixels at frames 0/2/8/30. The color-channel tolerance is two levels.
The area and density bounds account for fractional antialiased edges
while rejecting a hollow shape or dispersed pixels with a misleading
centroid. The original FadeIn controls at 0/15/30 remain in place.

This strengthens the validator; physical X/Y evidence is pending a new
source-pinned run and authenticated original readback. The historical
frames are not rendered or decoded again to claim the new coverage.
The direct/mediated comparison still has its separate 60-pair RGBA
decode; only the X/Y oracle shares one crop for its two measurements.

The 12 focused pure pixel-validator tests include individually frozen
and linear axes, instantaneous jumps, swapped displacements, incorrect
endpoints and misleading shape/color data. They exercise the validator
only. Synthetic control bytes cannot count as Native evidence or human
aesthetic approval.

MW05-E01-01 remains PARTIAL: general multi-key curves, shape scale,
rotation, masking, 3D camera/perspective, text rendering and complete
human acceptance are not established by this bounded production case.
The 60 independent product acceptance cases remain NOT_RUN.
