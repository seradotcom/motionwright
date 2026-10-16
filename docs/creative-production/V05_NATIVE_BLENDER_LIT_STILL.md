# Motionwright v0.5 — owner-provisioned, physically lit Blender still

**Requirement MW05-E07-04 — bounded technical partial.**

The existing Motionwright Native SDK pipeline already realizes original
Canvas geometry as typed Blender primitives and exports a verified GLB.
That is editable geometry evidence, **not an image or cinematography**.
This continuation adds a separate physically rendered PNG through the
**same pinned Semwright Blender Driver Host**, source-scoped to that
verified GLB and the exact project generation/revision.

## What can be produced

The new first-party Rust operation
`ProductionCoordinator::render_blender_lit_still` runs **after** an
original native GLB contribution is already verified. It rejects an
unknown source project, foreign session, stale SQLite revision,
unverified prior GLB receipt or non-Blender scene.

Instead of silently constructing a studio rig, it checks the **live
owner-provisioned Blender session**:

- `driver.blender.scene.inspect` must observe the active camera.
- `driver.blender.object.list` must return an untruncated inventory,
  matching that actual CAMERA object and at least one LIGHT.
- The owner must declare that this existing camera/light is intended
  for this preview. This declaration is **not** an independently
  authenticated owner/source-right signature.

Only then the existing Semwright Host executes
`driver.blender.render.settings` using **Cycles on CPU**, dimensions
320–1920 × 180–1080, at most 2,073,600 pixels and 4–32 samples,
followed by `driver.blender.render` to a non-overwriting,
SHA-256-derived basename inside the owner provisioned output root.

The application inspects the original 8-bit RGB/RGBA PNG IHDR,
dimensions and immutable SHA-256 under the existing hardened artifact
path and link checks. Its result binds project ID, generation, revision,
scene ID, original GLB hash, observed camera and light identities,
physical PNG bytes and profile. The persisted Semwright operation
receipts retain the exact commands, request keys, native provider
provenance and uncertain-result semantics. Retrying a non-idempotent
render does **not** overwrite an existing image.

## Physical GitHub Actions acceptance

Rather than introducing another renderer or a duplicate heavy CI,
the existing disposable-runner native Blender E2E now additionally:

1. Builds the **exact pinned** Semwright Broker, Blender Driver Host,
   Blender session runner and Motionwright Native SDK consumer.
2. Realizes the same two original editable components and independently
   verifies the GLB as before.
3. Executes the new owner-provisioned Cycles/CPU still step in the same
   broker session (Blender 4.5.14 LTS), checks actual camera/light
   inventory, 640 × 360 native RGB(A) PNG and output SHA.
4. Decodes the PNG through Pillow to prove a nonuniform image, then
   preserves both GLB and PNG as review artifacts.
5. Records truthful flags: neither artistic quality, camera/light
   originality, source usage rights, video completion nor publication
   was verified by an independent person.

The smaller Rust adversarial tests cover missing camera, missing lights,
truncated inventory, invalid dimensions/samples and unauthorized owner
declarations. E2E proves real render **only if exact-SHA CI succeeds**.

## Important limits

- This does **not** create/edit cameras, lens profiles, three-point
  lighting, path-traced animation, 3D rigs, render farms, HDR grade,
  motion blur or lighting continuity across cuts. Those Semwright
  capabilities are not assumed in this bounded contract.
- There is **no production Studio UI for arranging a lighting rig**;
  this is an exposed first-party Rust production operation and an
  installed native E2E verification, not a full 3D cinematography UI.
- No new Native SDK version, direct Blender Python invocation or
  shell/operator escape is introduced. Only four existing pinned
  allowlisted Blender capabilities are admitted to Motionwright's
  explicit production command list.
- A live camera/light in Blender cannot by itself prove artistic taste,
  true studio illumination, legal rights or user satisfaction.
- The 60 human creative acceptance cases and cross-renderer comparison
  remain NOT_RUN. `release=false`.

**Status: E07-04 PARTIAL if CI verifies the real PNG.** A clean
source/static test does not constitute physical Blender acceptance.
