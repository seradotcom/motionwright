# Motionwright v0.5 — preliminary visual review (synthetic first-party sources)

**Provenance:** GitHub Actions run `38013902788`, Motionwright source SHA `608777b0f7b90ad374d46c29b72e519b73941e1a`, artifact `native-creative-review-shard-0-608777b0f7b90ad374d46c29b72e519b73941e1a`. The images inspected were the actual source-bound PNG samples `hero-reveal-landscape-en/frame-000060.png`, `causal-diagram-portrait-es/frame-000060.png` and the 3×3 `contact-sheet.png`, not generative renderings or paper prototypes. The source is synthetic. No one is authorized to advertise it as a demonstrated Motionwright product flow.

This is a **preliminary technical-design audit** of these three images. It records observable deficiencies and hypotheses, **not independent user-study evidence** or a claim that every temporal frame/reformatting behaves the same.

## ProductHeroReveal: good source retention, weak actual storytelling

- At 640×360 the left body line is visually far smaller than the primary title. It is difficult to read at one-to-one video size; the disclosure at lower left is more visible but still secondary. This is a concrete legibility issue despite native font coverage and non-clipping CI passing. Formal acceptance should impose a readable minimum size **by semantic text role**, not one global token shrinking long strings to fit. A source-level `BrandProfile.minimum_body_size` of 18 is already recorded, but the helper can generate smaller text.
- The right side uses original mock interface rectangles within a dark rounded stage. The annotation indicates `GRAPHIC STUDY / NOT PRODUCT EVIDENCE`, which is important and must remain. The interface does not show a verifiable user task, meaningful before/after or software operation.
- The copy `Make the work. Keep the craft.` is a brand assertion, not an explanation of the product's main value. A production hero must explicitly connect audience problem → observable action → outcome while avoiding fictional performance claims.
- Actual review must compare several meaningful layout/design alternatives with real authorized captures and measure typography, camera/motion curve, hold duration and causality. This sample alone does not meet the v0.5 premium visual goal.

## Causal diagram: no legible explanatory semantics yet

- The 360×640 Spanish frame shows an upper title and a group of three unlabelled rounded blocks connected horizontally. No visible labels define causes, consequences, quantities or direction. Viewers cannot infer a specific causal mechanism merely from the geometry.
- Layout is spacious but does not use the surplus space to explain a data relationship. Retaining editable blocks and paths is a source-level success, not evidence that `CausalLab` produces a useful instructional film.
- Proposed acceptance: at least one true/source-attributed explanatory relationship (or explicitly fictional illustration), authored labels for each semantic entity, visible transition from cause to effect across several sampled frames and a human comprehension review in Spanish.

## Additional representative cases visible on the contact sheet

- `mask-window-square-en` and `hard-cut-hold-square-en`: pale blue rectangular regions dominate with little independent visual meaning. They may be valid primitives but should not be counted as finished creative components without use in an actual composition.
- `comparison-portrait-de`: contrast words `Vorher`/`Nachher` are visible, but no owner-sourced before/after visual is present. Treat as a template for a future authorized comparison, not as a validated demonstration.
- `caption-emphasis-portrait-es`: split-copy treatment shows more authored motion potential; words are small in the reviewed contact thumbnail. Needs one-to-one PNG and hold-duration review before claiming readability.

## Release and human acceptance rules

- Keep successful rendering, source identity, frame counts and absence of clipping as **technical** evidence. Do not treat these as independent creative approvals.
- Start full quality acceptance with **one excellent ProductHeroReveal** composed from authorized media, accessible body type, a visible action/outcome, deliberate camera movement and copy appropriate to landscape/portrait/square. Prove ten revisions without losing that design, then apply learnings to the remaining library.
- Compare the actual finished source against a competent designer/agent using HyperFrames directly. The test must measure result quality and retained human edits, not simply number of supported features.
- Native Blender, original WAV, responsive layouts and Studio need separate human review; this report covers only the listed screenshots.

**State:** technical recipe/render PASS on the referenced source, sampled visual human/art-direction approval **NOT REVIEWED**, production-ready status **BLOCKED** pending the full set of evidence.
