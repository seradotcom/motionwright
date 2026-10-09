# Source-preserving static camera pan in canonical Film

Motionwright's Native SDK Film adapter can now represent a **static camera translation** in a real Semwright Motion Canvas production plan when the camera zoom is **exactly 1.0** and camera rotation is **exactly 0.0**. This is a narrowly admitted and testable subset of authored camera state, not a claim that every camera or keyed motion curve is supported.

## Exact transform

The app-owned project Canvas uses a 1920 × 1080 world coordinate system and a camera with `center_x`, `center_y`, `zoom`, `rotation_deg`, `safe_margin`. With zoom 1 and rotation 0, camera translation is exactly the same as translating every object by:

- `dx_world = 960 - camera.center_x`
- `dy_world = 540 - camera.center_y`

To project into any saved delivery profile, the existing Film layout first determines the strict `position_scale_x`, `position_scale_y`, `object_scale` and output offsets according to the output's explicit Replan/Crop strategy. The native adapter adds the camera translation **per scene** in that same projected position space:

- `offset_x += dx_world × position_scale_x`
- `offset_y += dy_world × position_scale_y`

This is applied to the existing `Subject` fixed layout for all node kinds before Semwright's pinned deterministic `realize` compiler is invoked. It preserves source CanvasNode identity, original world coordinates, font type scale, shape/stroke size, z-order, segment duration, frame count and selected scene cut. Unlike shifting a rendered bitmap or guessing a screen-space CSS transform, it passes the translated semantic positions to the native Film contract.

**Safe area is checked after camera translation** against the actual selected output profile. A pan that moves an object outside the verified Replan frame refuses production instead of clipping or hiding it. The existing Crop profile keeps its explicit intentional crop semantics, not a fabricated Replan guarantee.

## Fail-closed boundaries

Zoom other than 1 and camera rotation other than 0 still return `Unsupported` before the native render. Nonrepresentable object keyframes, transforms, opacity, blend modes, coordinate spaces, foreign text fonts, or semantic relations keep their existing independent rejection rules. An editor may show a zoomed/rotated *semantic preview* without claiming that Film can render that exact view.

Each scene can use a different **static** pan in a multi-scene Film sequence because the offset is applied to its own Shot's subject coordinates, not globally across the whole segment. This represents discrete scene-specific static framing, not an interpolated camera move during a scene. No SDK capability, request authority, renderer tool chain or user consent model changed.

## Evidence and current limitations

Rust native Film tests build real project fixtures and compare exact realized Film subjects against identity-camera fixtures, validate distinct per-scene X/Y deltas, unchanged source object transforms, unchanged type scale/frame count, and rejection of unsafe framing, zoom and rotation. The standard Semwright pinned canonical Film, actual Motion Canvas E2E and native AV master CI lanes remain mandatory. They certify the existing renderer path against their fixtures; end-user visual acceptance and pixel-exact parity for every creative composition remain unexecuted.

This admission is compatible with the existing read-only [Film semantic preflight](film-preflight.md): unsupported combinations are rejected before allocating a Semwright render job, and supported static pans still require a real verified render for an evidence claim.
