# Canonical Motion Canvas Film projection

Motionwright does not reinterpret every scene as Motion Canvas. Production first partitions the selected deliverable cut by renderer. Only contiguous scenes explicitly owned by the Motion Canvas renderer are projected into Semwright's pinned semwright-motion-authoring::Film contract. Blender, Manim Community and other renderer scenes retain their renderer identity for their own native lanes.

## Projection rules

The projection is intentionally strict:

- a Film is at most 600 seconds and 32 sequences; longer Motion Canvas runs are partitioned at scene boundaries;
- the selected deliverable controls width, height and exact rational frame rate;
- landscape, portrait and square outputs are supported through deterministic semantic replan; crop is a distinct profile strategy that requires explicit approval;
- a custom narrative cut preserves source scene order and is reflowed onto a zero-based deliverable timeline;
- localized text is selected from profile-owned object overrides and enters Film with the profile locale;
- native text carries font-loaded and no-truncation constraints; Motionwright does not silently clip a longer translation;
- primary font, mono font, narrative role and archetype remain explicit inputs rather than inferred claims;
- Motionwright's 1920x1080 project-pixel canvas is projected into the selected output profile with deterministic positions, object scale and safe-area checks;
- text, rectangle/shape, circle and structural group nodes are supported;
- rotation, partial opacity, non-normal blend modes, non-project coordinate spaces, unresolved semantic relations and unsupported node kinds fail closed instead of being dropped;
- static camera **pan** with zoom exactly 1 and rotation exactly 0 is admitted by the exact [Film scene translation mapping](native-film-static-pan.md), with safe-area checks after the offset; nonidentity zoom, camera rotation or unsupported animation still fail closed;
- asset digests remain SHA-256-bound canonical asset references;
- every generated Film is passed through Semwright's deterministic realize compiler before it can enter production, including exact-frame-boundary verification.

A generated Film is authoring intent. It is not renderer evidence. Renderer execution, readback, Graph admission and Effect Conformance stay in their respective Semwright-owned authority paths.
