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

Blender is a committed second renderer for meaningful 3D work: geometry, materials, camera, animation and 2D/3D handoff. It must use Semwright's Blender driver and readback rather than hidden Python mutation.

## Manim Community

Manim Community is a separate semantic realization for mathematical/diagrammatic scenes. Model output selects from a fixed vocabulary; it does not provide arbitrary Python source for execution.

## Optional paths

Remotion and ManimGL can be adapters when licenses/environment permit. Their absence must not block the required no-paid-provider path.

## Renderer swap

Swapping a renderer computes capability differences before executing. Losses are explicit and reviewable. A backend that can only crossfade a concept transition must not report an exact morph.
