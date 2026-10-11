# Exact source-authored linear opacity entrance (native Motion Canvas)

Motionwright's Canvas editor already has editable per-node opacity keyframes and an editorial playhead preview. This slice admits **one exact source-authored opacity-only fade** into the canonical Film rendered through the pinned Semwright Native SDK (`SOURCE_LOCK.json`). It does not add an independent rasterizer, mutate preview pixels into "proof", or reinterpret an unsupported curve.

## Admitted editable source

A single unparented non-group Canvas node with persistent opacity `1.0` and no source descendants may carry exactly two **linear** opacity keys: `(scene-local 0, 0.0)` and `(positive frame-aligned scene-local t, 1.0)`. The second time must be before the scene's end. The scene cannot have split authored beats or any other keyframe channels on that node. Rotated nodes, partial opacity endpoints, three-key curves, holds, easing substitutions and mixed transform/opacity paths are **unsupported** rather than silently approximated.

The source remains an application-owned Canvas node, editable through ordinary revision/CAS and lock rules. The Film projection emits one absolute, exact rational `TemporalSpan` and `Primitive::FadeIn` with `MotionEasing::Linear` targeting the same canonical node ID. The pinned motion-authoring compiler emits a native Opacity tween from `0.0` to `1.0`; other shapes, source text and native X/Y settle motion retain their existing behaviors.

## Evidence and limits

The native-render E2E creates a real application-owned 1920×1080 project with a separate opacity rectangle, the previous independent linear X/Y rectangle, and source text. It renders through the Semwright Broker/Driver Host and checks the exact 60 PNG entries against the native frame manifest. **For this fade specifically**, FFmpeg only decodes an isolated, fixed 180×90 RGB24 rectangle in real native PNGs at frames 0, 15 and 30. A progressive halfway observation is required, rejecting static or abrupt output. The same-SHA verification file records ROI SHA-256s and byte distances, not a universal visual quality score. FFmpeg is an acceptance inspector, **not** a substitute render backend.

This is evidence for a single linear opacity subset on a pinned native fixture, not a completed general-motion implementation or human-accepted creative edit. Alpha compositing with arbitrary overlays, color-management edge cases, fade-out, mixed channels, arbitrary curves, hierarchies and additional devices/formats still need independent property fidelity and real output acceptance. An authored keyframe that cannot be mapped exactly fails with Unsupported.
