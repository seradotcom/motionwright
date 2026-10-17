# v0.5 — Source-bound typography and safe-area preflight

This adds a **read-only** review surface to Canvas → Typography safety.
The source is the canonical Project generation, revision, Scene, selected
DeliverableProfile, current scene-relative playhead and authored CanvasNode
text. It does not create project revisions, export media, approve visual
quality or use a second renderer.

## What the preflight checks

- Every first-party text node in the scene, including human-edited copy.
- Bounding rectangles projected through the **semantic camera**, including
  camera translation, zoom, rotation and node-local rotation.
- Whether sampled text rectangles extend outside the 1920 × 1080 authored
  source canvas or cross the scene's stored camera.safe_margin.
- Explicit multi-line declared line-box height, using the authored
  font-size and line-height values. Glyph width, kerning and font
  fallback are not estimated or asserted.
- Keyframe endpoints, the current editor playhead and midpoints between
  actual keyframe times. The number of keyframes is bounded; unsupported
  or invalid time data leads to **Needs native review**, not an
  invented "safe" result.

The panel shows why each item is flagged and selects the exact original
object in the existing inspector, where the user can adjust its authoring
parameters with the same revision, lock and Semwright Native SDK authority
controls already built into Motionwright.

## Explicit scope boundaries

The semantic stage is 1920 × 1080. 16:9 delivery profiles with
corresponding aspect ratios permit *geometric preflight only*. For
9:16, 1:1 or other aspect ratios, Motionwright must replan or crop
based on real target layout; the preflight reports an **unresolved**
format-specific projection instead of stretching 16:9 coordinates and
pretending that portrait typography is correct.

Text overrides and crop-mode profiles also require native inspection,
even when the declared output has the source aspect ratio. Nested
objects, coordinate-space changes, relationship constraints,
invalid cameras, incomplete fonts, oversized motion sets and excluded
scenes fail closed to **Needs native review**. A sampled_inside
finding proves **only** that sampled rectangle bounds did not cross
these specific source margins. It does not establish that all
interpolation moments are safe, the glyphs fit, the font is installed
or licensed, captions are readable, native raster frames match the
editor, motion is legible, or a creative reviewer accepted the work.

Renderable visual evidence still comes from the pinned Semwright Native
SDK → Broker → Driver Host, native readback and independent pixel/codec
checks. This panel does not call the Broker, grant effects, change the
Native SDK pin, emit new assets or manufacture visual PASS signals.
The 60 separate independent product acceptance cases stay NOT_RUN.

## Tests and UX quality

The unit tests cover geometry, margin crossings, explicit lines,
keyframe sampling, camera zoom, portrait/square refusal, variant
exclusions, unsupported node relationships, absent text and failure
to mutate the project. Playwright exercises Canvas expansion,
source-bound report navigation and live response to a revisioned
camera-safe-area edit in Chromium. CI executes unit/build, browser
and full existing native E2E workflows at the exact PR SHA.

The control uses the existing Cut Room Ledger theme tokens and
the editor styles documented in [DESIGN.md](../../DESIGN.md). It is a compact expandable
inspector, not a dashboard, modal takeover or decorative review score.
