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

Run [44096761232/1](https://github.com/seradotcom/motionwright/actions/runs/44096761232)
completed this physical X/Y case with Motionwright source
3586d08552096573dbdcb3f1893ff401709cddde and Semwright
e60e789035b1afe02df127e510b6be533371f567. Two distinct Native jobs
produced the 120 original PNGs, all 60 direct/mediated RGBA pairs matched,
and Host PID 13600 closed with exit code 0 and a quiescent process group.
Both axes passed at frames 0/2/8/30; the largest observed centroid error
was 0.2318518518518431 output pixels, within the 1.5-pixel bound.

The independent local metadata read authenticated all 460 snapshot
members and rechecked 130 original references after closure: eight
source/result documents, two manifests and 120 PNGs. It preserved
project 01a1e9ca-403c-7791-80a9-c2b67526dca3, generation
01a1e9ca-403c-7791-80a9-c2b7dc80c0e9 and revision 7. Its original report
SHA-256 is d3a0a20b05f8aa6e2001e4decb7589c0a46bf2bbbc1bc9df408036ce8091df63,
retained with the evidence for run `44096761232` as
`independent-mc-original-review.original.json`.
This read performed no Native operation or additional media decode and
does not grant formal G2/G3, human visual acceptance or release authority.
The evidence belongs to the exact source above, including after later
documentation or integration commits.

The historical frames were not rendered or decoded again for this
coverage. The original direct/mediated comparison has its separate
60-pair RGBA decode; only the X/Y oracle shares one crop for both axes.

The existing native-render-e2e workflow also has a closed out-cubic-xy
development selection. It requires a manual first attempt, the exact
published source SHA, the SDK pin in SOURCE_LOCK, and an admitted public
RSA recovery recipient. This selection permits only the original stage,
two distinct render jobs and one Host: 60 full frames per leg, 120 total.
The editable-source-cycle selection retains its separate four stages;
out-cubic-xy cannot enter its edit or reopen commands.

Before Host closure, a private witness binds the original source project,
Film, selected-SDK binding receipt, manifests and all 120 PNG files by
size and SHA-256. After closure, the read-only finalizer checks these same
originals, both axis measurements, complete frame parity and process
identity. It performs no additional render or media decode. Owner,
evidence and preflight originals remain in one authenticated encrypted
capture; only the ciphertext and its closed public manifest are uploaded.
This development case grants no formal or human acceptance and makes no
new edit/reopen claim.

The 12 focused pure pixel-validator tests include individually frozen
and linear axes, instantaneous jumps, swapped displacements, incorrect
endpoints and misleading shape/color data. They exercise the validator
only. Synthetic control bytes cannot count as Native evidence or human
aesthetic approval.

MW05-E01-01 remains PARTIAL: general multi-key curves, shape scale,
rotation, masking, 3D camera/perspective, text rendering and complete
human acceptance are not established by this bounded production case.
The 60 independent product acceptance cases remain NOT_RUN.


## MW05-E01-01 — edit and save a frame-exact held shape

The existing Canvas typed keyframe editor also authors Hold for an unparented
rectangle/circle/shape. For the bounded two-second, 30 fps geometry case, the
initial X, Y, width, height and rotation keys are linear at frame 0. Seek the
existing frame-30 key, select each property, retain its authored destination
value, choose Hold and use **Add or replace keyframe at playhead**. Each explicit
Save advances the real Project revision. All five destination keys must use the
same supported interpolation before the Native Film is eligible; mixed curves
are refused. The destination values remain the source object's base transform.

The existing one-effect geometry runner has a closed `hold` profile. It opens
its existing real SQLite Project in Studio through the original Service test
transport, performs the five UI Saves, reloads Studio to read the saved held
keys, closes the writer with its normal read-only handoff and captures that
exact revision through StudioServiceReader. It then uses the existing standard
Semwright Broker/Driver Host to render the one 60-frame Film. The source capture
requires eight zero-duration Native Set operations at frames 0 and 30, with no
Tween substitution. The final independent reader verifies the unchanged
original Project, history and physical Store after the rendering writer exits.

Hold verification reads original PNG geometry at frames 0, 29 and 30, using the
same spatial tolerances as the existing geometry oracle. It additionally hashes
every original PNG and requires frames 0–29 to have identical complete PNG
bytes, frames 30–59 to have identical complete PNG bytes, and the two intervals
to differ. This catches a transient intermediate frame or a late/early jump;
whole-file equality of lossless PNG originals entails equality of their decoded
frames. The unchanged OutCubic profile retains samples 0, 15 and 30.

The Studio step precedes any Native owner launch and cannot grant or invoke a
Native render. Missing, stale, incomplete or UNKNOWN source/Save/closure results
stop the selected execution. Neither profile automatically retries effects.
The source receipt binds the original Studio observation by SHA-256; raw grants
and service transcripts stay private. This development path does not establish
installed-Tauri operation, independent human visual approval, G2/G3 or release
authority. A completed original run must be identified by its actual source and
run before claiming the physical Hold path passed.
