# Canonical Motion Canvas Film projection

Motionwright does not reinterpret every scene as Motion Canvas. Production first partitions the global project timeline by renderer. Only contiguous scenes explicitly owned by the Motion Canvas renderer are projected into Semwright's pinned semwright-motion-authoring::Film contract. Blender, Manim Community and other renderer scenes retain their original renderer identity and timeline placement for their own native lanes.

## Projection rules

The first production projection is intentionally strict:

- a Film is at most 600 seconds and 32 sequences; longer Motion Canvas runs are partitioned at scene boundaries;
- the selected deliverable must remain 16:9 for this projection; vertical and square output require a separately reframed creative branch rather than automatic stretching;
- frame rate, primary font, mono font, narrative role and archetype are explicit inputs rather than inferred claims;
- Motionwright's top-left 1920x1080 canvas coordinates are converted deterministically to the canonical centered coordinate system and may scale uniformly within 16:9;
- text, rectangle/shape, circle and structural group nodes are supported;
- rotation, partial opacity, non-normal blend modes, non-project coordinate spaces, unresolved semantic relations and unsupported node kinds fail closed instead of being dropped;
- non-default camera pan/rotation/zoom fails closed until the canonical mapping can preserve it exactly;
- asset digests remain SHA-256-bound canonical asset references;
- every generated Film is passed through Semwright's deterministic realize compiler before it can enter production, including exact-frame-boundary verification.

A generated Film is authoring intent. It is not renderer evidence. Renderer execution, readback, Graph admission and Effect Conformance stay in their respective Semwright-owned authority paths.
