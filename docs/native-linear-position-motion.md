# Exact linear X/Y keyframes in native Semwright Film

Motionwright now maps a **precisely delimited subset of Canvas position keyframes** into the pinned Semwright Motion Canvas Film contract, rather than silently flattening all authored motion to a still image. General keyed animation, rich easing, layout motion, camera keyframes and renderer-neutral timeline compositing are **not** claimed here.

## Admitted authoring pattern

For a single unparented Text/Shape/Rectangle/Circle canvas node within an otherwise valid Motion Canvas scene **without authored scene beats**, Film admits exactly four versioned scene-local motion records: 

- X and Y at **time 0**, providing the initial position in project pixels.
- X and Y at **the same positive time T**, where **both equal the node's unchanged base X/Y** and T is strictly less than the scene duration.
- All four records must specify **linear** interpolation. The endpoint T must fall on an **exact native output frame boundary** at the selected rational FPS; fractional timing is validated without floating-point rounding.
- No other channels or keys. Parent/child/group animation, simultaneous independent position animations, beat spans, hold, smoothstep `ease_in_out`, arbitrary target positions and non-frame-aligned endpoints remain Unsupported.

This very restricted motion is exactly equivalent to Semwright's bounded `Primitive::Settle` with `rotation: 0` and a native Position tween from `OriginalOffset(Point(dx,dy))` to `Original`, with `MotionEasing::Linear`. It renders from the authored initial position to the author's base layout from t=0 to T, then remains at the base position for the rest of the scene. The rotation tween is identically zero and introduces no visual rotation.

The source Canvas node and keyframes remain intact and authoritative. Motionwright constructs a separate `Invocation`, rational-time `TemporalSpan` and a containing constraint bound to its scene's actual Shot. The native Semwright `realize` compiler validates the resulting motion instruction IDs, time intervals, subject references and channel concurrency before the plan can be dispatched.

## Coordinate and safety policy

Offsets are transformed through the output's existing explicit Replan/Crop policy: `dx_film=(x_at_0-base_x) × position_scale_x`, similarly for Y. The endpoint subject keeps the exact static native subject position and font size. The existing per-scene static camera pan is preserved by applying the same camera translation to the static subject and initial-position safe-area test.

For Replan, the **initial and final node bounds must each fit inside the versioned safe area**. Because native position interpolation is strictly linear and object bounds do not change, all intermediate frames stay in the convex safe rectangle without hidden clipping or size changes. Unsupported motion never produces a best-effort approximation; the exact read-only Film preflight reports Unsupported without dispatching the pinned driver.

This feature budgets one extra temporal span per admitted animated node, checked against the existing 128-span Film ceiling. It does not broaden file privileges, enable arbitrary JavaScript/Python, change the Semwright Native SDK pin, or create another renderer/backend.

## One-operation authoring

Motionwright's Canvas inspector exposes the same source-bounded pattern as an explicit **Create native linear move** action. The caller specifies start X, start Y and end frame for a saved deliverable profile. The canonical Rust Change `set_canvas_linear_position_motion` creates the four rational keyframes in **one store transaction** with the existing project revision CAS and scene/node locks. The public Native SDK capability `canvas.motion.linear-position.set` exposes this exact operation to agents, with closed typed parameters. Existing keyframes are never overwritten, a move without displacement is rejected, and the authoring operation does not claim the Film renderer has run until its separate preflight and Semwright production evidence pass.

## Verification

- Rust native Film tests compare source positions with native `Primitive::Settle` and the deterministic `realize` Position Tween, verify rational timing, unchanged creative base state/frame count and explicit refusal for incomplete/mixed/eased/off-frame/unsafe keyframes.
- The existing **real Semwright Broker→Driver Host→Motion Canvas E2E** fixture now includes a visible, unparented 2D tile with linear X/Y motion from frame 0 to frame 30. It produces 60 actual native frames and checks every frame against the returned artifact manifest SHA-256. The E2E additionally requires frame 0 and frame 30 to have **different SHA-256 digests**, rejecting a silently flattened still-frame render. Its Motionwright and Semwright sources remain exact-SHA pinned in GitHub Actions.
- The same animated seed goes through the pinned MLT H.264/AAC final-master E2E, preserving the existing real video/audio checks. A pass demonstrates this bounded live rendering scenario, **not** frame-perfect parity for all creative content, a human-reviewed rendered output, or 60 independently completed product acceptance cases.

## Still unsupported

Interpolation `ease_in_out` in Motionwright's Canvas uses smoothstep `t²(3−2t)`, which is **not identical** to Semwright's native in/out cubic easing. Consequently, this mapping does **not** silently substitute the native easing for that curve. Hold, generic multi-stop motion, interpolation of arbitrary channels, parent-group motion, scene beats, multi-renderer video graphs, arbitrary motion paths and complex camera animation require separately verified renderer mappings. They remain explicitly unsupported until fidelity is demonstrated.
