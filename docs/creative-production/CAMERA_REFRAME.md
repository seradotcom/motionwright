# MW05-E07-03 — Original planar camera reframe

Status: partial implementation. Not a 3D orbit, dolly, focus, optical zoom or lit shot.

Motionwright now records bounded, owner-authored start and target camera centers
for an original Motion Canvas scene. Each move uses 12–180 exact frames at
30fps, a maximum 80 original-Canvas-pixel movement per axis, at most one
move per scene and 16 per project. Project CAS and the same Semwright Native
SDK application own its edit, generation, locks and revision.

At native projection, the existing canonical Film pipeline keeps original
semantic subjects, text groups, layers and IDs. It realizes the start camera
position as one shared Settle translation on every subject and returns all
subjects to the persisted terminal camera pose over one real temporal span.
Both endpoints are checked against the selected output safe area; conservative
source bounds prohibit cropping before output selection. Parent/child groups,
existing object keyframes and multiple authored beat spans are not admitted
until independent conflict-free native realization is verified.

The Production Camera editor exposes exact source center/duration, staleness
warnings and an editorial view at the original project aspect. It does not
pretend its SVG preview proves a rendered MP4 or new cinematography capability.
Detaching the reframe preserves the final static scene camera and all objects.

Tests: Rust source/bounds/lock and JSON roundtrip, native Film semantic
motion/spans, TypeScript parity, and browser negative controls. Full source-
pinned MP4 pixel verification, more renderers, frame rates, multi-scene
camera tracks, authentic optical camera rigs, parallax, lens and light are
not complete. Human creative approval is still required.
## Real native camera technical evidence

A separate source-pinned GitHub Actions workflow renders an original
six-second camera project with the exact Semwright Broker, Motion Canvas,
MLT and FFmpeg. A 90-frame authored camera pan is verified from native
PNG edge pixels and independently decoded H264 MP4 samples; the result
is linked to the original project generation, commit and source SHA-256.
Five adversarial image tests reject static, opposite-motion, blank, or
wrong-viewport evidence; no synthetic calibrator is promoted as native proof.

The camera visual acceptance remains pending until the exact branch SHA
passes the pinned workflow. Even then, a 2D pan is not an optical dolly,
3D orbit, custom lens, cinematic lighting or human creative approval.
