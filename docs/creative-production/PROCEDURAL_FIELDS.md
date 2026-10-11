# MW05-E06-04 — deterministic procedural field, bounded implementation

This is a real editable Canvas authoring capability within **Production**, not an opaque
generated SVG/video, extra renderer, scheduler, Core or Platform dependency.

## Contract and deterministic implementation

The versioned `ProceduralConfig` defines an integer `seed`, `count`, `columns`,
`distribution` (`grid`, `staggered`, `scatter`), origin, extent, square size,
opacity percentage, #RRGGBB fill, and optional 30-fps synchronized Y/opacity entrance sequencing
(`reveal_step_frames` 0–10; 0 disables, `reveal_duration_frames` 1–60).
Every generated item is a real, persistent,
separately editable Canvas `rectangle` with a stable UUID, a source baseline,
style, z-index and coordinate-space metadata.

- The authored stage is 1920 × 1080 project pixels; all generated squares remain
  inside its bounded area. Scene nodes are rendered by the existing Motion Canvas
  route and may be changed normally in Canvas, then exported through the
  existing native rendering/receipt path.
- Per-field limit: **1–64 objects**; **1–16 columns**, **4–128 px size**, **1–100%
  opacity**; **at most 16 fields per project, one per scene**. Validation rejects
  impossible cell geometry, too-large frame bounds, unsupported distributions,
  and out-of-range integers before generating nodes.
- Grid is centered cell repetition, staggered adds deterministic alternating row
  offset, scatter samples within each cell using 32-bit wrapping integer
  hashing. The seed affects scatter; it intentionally does not affect grid
  alignment or fixed staggered rhythm.
- Generated UUID = SHA-256 of `motionwright.procedural.v1\0`, the field UUID,
  then the four-byte **little-endian** index, with version-8 and RFC variant
  bits set. The same index retains its identity across seed/layout/count changes.
- Optional entrance keys use the native-admitted synchronized Y/opacity grammar:
  HOLD at frame 0, optional HOLD at `index × step`, then EASE_OUT_CUBIC
  to the original Y position and 100% opacity at `start + duration`.
  Initial Y is displaced +12 project pixels to create a visible SlideIn.
  Rational time is canonicalized in 30fps fractions. Animated fields with
  opacity below 100% fail closed, because Film cannot preserve terminal alpha.
  The last key must remain inside the half-open scene interval; otherwise
  the command fails before allocating any authored nodes.
- All generated items share one z-index, -32, rather than exceeding the
  native Film limit of 32 distinct layer orders. Static alpha below 100%
  remains editable but is explicitly rejected by native Film.
- Rust and TypeScript independently implement this specification. Tests in both
  languages assert identical expected coordinates and IDs for a 12-node seeded
  fixture. The generator does not access global RNG, system time or cloud APIs.

## Revision, human editing and source safety

`upsert_procedural_field` and `detach_procedural_field` are existing project
change transactions. The existing project/scene locks, generation/revision
authority and persistent branch/undo structures remain authoritative.

Updating a field uses its prior generated baseline to merge compatible
per-property human changes. A conflicting human edit is never silently
discarded. Adding items only appends a stable new index. Shrinking requires that
the removed suffix is unmodified and unreferenced by any other node; otherwise
the whole update fails. Deleting a generated node independently makes a
subsequent generator update fail until the node is restored or generator detached.

**Detach is intentionally non-destructive:** it removes the procedural generator
link and retains all already generated Canvas nodes for manual authorship.

The panel shows an explicitly labeled **editorial preview**, not a native
renderer acceptance receipt. Motionwright's ordinary native render and video
validation must still be run for any quality or fidelity claim.

## Current boundary

This slice implements editable repetition, two-dimensional spatial distributions,
and a bounded native-admitted Y/opacity OutCubic entrance sequence. **More expressive temporal fields,
velocity/forces, 3D instancing, multishot orchestration, masks,
GPU acceleration and independently reviewed native rendering fidelity
remain out of scope.** Even when CI passes, MW05-E06-04 stays
partially completed until the remaining families and real aesthetic QA are
delivered. No accelerated GPU/CPU performance claim is implied by the object
count limits.

Tests: `crates/domain/tests/procedural_fields.rs`,
`crates/native/tests/procedural_fields.rs`,
`apps/studio/src/proceduralField.test.ts` and
`apps/studio/tests/procedural-field.spec.ts`. Resource-heavy tests, native
rendering and packaging run on GitHub Actions, not the author's workstation.

The Semwright Native SDK application driver exposes typed, revision-bound `procedural-field.upsert` and `procedural-field.detach` operations. No parallel backend is added.
