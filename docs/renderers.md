# Renderer architecture

## Required path

Motion Canvas is the first semantic motion realization. MLT is the edit/master path for timeline assembly, audio and final media packaging through Semwright drivers.

The model does not assume pixel equivalence between backends. Each renderer declares:
- supported semantic actions;
- unsupported/lossy features;
- exact version/profile;
- deterministic inputs and seeds where possible;
- asset and font dependencies;
- output artifacts and readback coverage.

## Blender

Blender is the native 3D contribution path. Supported Motionwright canvas rectangles, shapes and circles compile into bounded mesh geometry, explicit RGBA materials and a dedicated collection. Production then uses only curated commands from the pinned Semwright Blender driver:

- `driver.blender.semantic.datablock.create`
- `driver.blender.semantic.objects`
- `driver.blender.mesh.geometry.replace`
- `driver.blender.semantic.object.create`
- `driver.blender.material.create`
- `driver.blender.material.assign`
- `driver.blender.export.glb`

The coordinator re-queries semantic refs after mutations instead of assuming old refs remain valid. Every accepted response must carry `driver:blender` provenance from Semwright. The exported GLB stays bound to the Motionwright project generation, revision and scene identity through local production receipts.

Unsupported text conversion, hierarchy, semantic relations, non-project coordinate spaces, opacity/stroke state and unknown node kinds fail closed. Motionwright does not expose arbitrary Blender Python or a generic operator escape hatch.

## Manim Community

Manim Community is a separate semantic realization for mathematical and diagrammatic scenes. Supported text, rectangle and circle primitives compile into a deterministic plan and generated source from a fixed vocabulary.

Model- or user-authored Python is never accepted as executable input. The compiler does not emit subprocess, eval, exec or dynamic import paths.

The pinned Semwright revision does not contain a Manim driver. This route is therefore **compiler-ready, native-driver pending**. Generated source is not called a native render, production PASS, Effect Conformance result or measured artifact until a Semwright driver executes it through the canonical runtime.

## Optional paths

Remotion and ManimGL can be adapters when licenses/environment permit. Their absence must not block the required no-paid-provider path.

## Renderer swap

Swapping a renderer computes capability differences before executing. Losses are explicit and reviewable. A backend that can only crossfade a concept transition must not report an exact morph.

Motion Canvas remains the canonical 2D authoring/render route and MLT remains the delivery/compositing route. Blender contributes explicit 3D artifacts. Manim contributes a safe declarative realization once a native driver exists. Motionwright does not create a second scheduler, Broker, Project Graph authority or Effect Conformance authority for any renderer.


## Optional renderer gates

Remotion and ManimGL are separate optional renderer identities. They are not dependencies of the
free base path and are not available merely because their names appear in a project. A project must
record a versioned extension descriptor with an exact SHA-256 digest, source, license/rights state,
requested permissions, and an explicit opt-in before either renderer can be selected.

Registering a descriptor grants no permissions by itself. Rights must be explicitly marked cleared
before opt-in, only one enabled descriptor per extension kind is allowed, and an extension cannot be
disabled or removed while a scene still selects its renderer. ManimGL never aliases or upgrades the
Manim Community path.

Optional generative-asset and catalog-package descriptors use the same registration boundary. Their
presence does not establish provenance, runtime execution, billing, network authority, or technical
acceptance. Those claims require their own evidence.

Interactive playback remains a future profile rather than a delivered player. No active pricing,
quota purchase, or commercial plan is inferred by Motionwright.
