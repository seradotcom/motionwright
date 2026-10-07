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

Blender is the native 3D contribution path. Supported Motionwright canvas rectangles, shapes and circles compile into bounded allowlisted primitives (`plane` and flattened `cylinder`), explicit transforms, RGBA materials and a dedicated collection. Production uses only curated commands from the pinned Semwright Blender driver:

- `driver.blender.collection.create`
- `driver.blender.object.create`
- `driver.blender.object.transform`
- `driver.blender.collection.link`
- `driver.blender.material.create`
- `driver.blender.material.assign`
- `driver.blender.export.glb`

Semwright 1.0 classifies arbitrary mesh-topology replacement as destructive and requires foreground human approval. Motionwright therefore does not use that path for its bounded rectangle/circle projection and does not attempt to bypass the approval boundary in CI. Every accepted response must carry `driver:blender` provenance from Semwright. The exported GLB stays bound to the Motionwright project generation, revision and scene identity through local production receipts.

Unsupported text conversion, hierarchy, semantic relations, non-project coordinate spaces, opacity/stroke state and unknown node kinds fail closed. Motionwright does not expose arbitrary Blender Python or a generic operator escape hatch.

## Manim Community

Manim Community is a separate semantic realization for mathematical and diagrammatic scenes. Supported text, rectangle and circle primitives compile into a deterministic plan and generated source from a fixed vocabulary shared with the Motionwright-owned Semwright Application Driver.

Model- or user-authored Python is never accepted as executable input. The compiler does not emit subprocess, eval, exec or dynamic import paths. The driver exposes only typed `doctor` and `render.start/status/cancel/result` capabilities under `driver:manim-community`. It delegates execution through Semwright Driver Protocol v8 to a SHA-pinned runner plus separately pinned Python and FFmpeg dependencies; render-time network authority is disabled.

The production coordinator binds every render to the Motionwright project generation/revision/scene, records normal Broker receipts, requires `driver:manim-community` provenance, verifies the plan digest returned with the job, and independently SHA-256 verifies the MP4 beneath the owner output root. The GitHub Actions `native-manim-e2e` lane is the real-runtime evidence gate: it provisions Manim Community 0.21.0, launches the provider through the real Semwright Broker/Driver Host sandbox and independently probes the resulting MP4. Source/compiler tests alone are not described as native-render evidence.

## Optional paths

Remotion and ManimGL can be adapters when licenses/environment permit. Their absence must not block the required no-paid-provider path.

## Renderer swap

Swapping a renderer computes capability differences before executing. Losses are explicit and reviewable. A backend that can only crossfade a concept transition must not report an exact morph.

Motion Canvas remains the canonical 2D authoring/render route and MLT remains the delivery/compositing route. Blender contributes explicit 3D artifacts. Manim Community contributes a safe declarative realization through its bounded Semwright Application Driver. Motionwright does not create a second scheduler, Broker, Project Graph authority or Effect Conformance authority for any renderer.


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


## OpenTimelineIO interchange

Motionwright exports a conservative OpenTimelineIO cut from the desktop runtime. The export writes a
Timeline containing one video Track and one Clip for each Motionwright scene. Scene duration is
preserved on a rational timeline and Motionwright identity, renderer, status and objective are kept
inside namespaced metadata.

The exporter deliberately uses MissingReference for abstract Motionwright scenes unless a scene has
an explicit, truthful media binding. It does not guess that a project asset belongs to a scene.
Canvas geometry, hierarchy, semantic relations, property locks, reviews, branch history, transcript
alignment, mix intent and renderer execution semantics are not described as lossless OTIO state.
Those omissions are returned as an explicit loss report and embedded in Motionwright metadata.

An OTIO export is therefore an editorial interchange artifact, not a round-trip replacement for the
Motionwright project bundle. The desktop command uses create-new semantics and never overwrites an
existing destination.
