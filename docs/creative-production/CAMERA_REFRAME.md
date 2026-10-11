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