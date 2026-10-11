# Creative production: implemented slice and review boundary

This is a **work-in-progress v0.5 expansion**, not a v0.5 product release or a claim that all planned creative capabilities are implemented. The application package version is unchanged. Project document format 2 protects the new creative state from older format-1 writers.

## What this branch adds

Production is a workstation within the existing Studio. It uses the same application-owned project, revision journal, SQLite transaction service, desktop effect grants and public Semwright Native SDK. There is no second scheduler, Core, paid model integration or Platform requirement.

The integrated baseline contains two first-party semantic compositions (`ProductHeroReveal` and `SplitExplanation`). This branch adds a third editable, user-authored `MetricEvidence` study, with an explicit source-not-verified disclosure. The creative slice includes persisted production plans, digest-bound opaque native-source attachments, scoped canvas proposals, explicit undo/redo as new revisions, and native frame inspection. The component supports three reflowed aspect families, editable text/brand color/motion controls and a conservative text budget. It is a **graphic study**, not a real software capture, a 3D product stage, or all desktop/mobile/CLI hero variants.

`requirements-status.json` tracks all 64 expansion IDs without replacing the original 208-requirement ledger. An implemented contract is not an automatically approved design. Code presence, integration tests, native evidence and human acceptance remain different fields.

## Start a design session

Only use desktop candidates built by an exact-source successful Candidate Packages workflow for the revision under review; do not mistake a pushed branch or pending CI for a verified installer. Keep production data backed up. Native rendering still requires the exact owner-provisioned Semwright runtime described in `../production.md` and `../native-sdk.md`; no paid account is needed for this slice.

The established native evidence workflow produces separate editable project bundles and six-second H.264/AAC masters for ProductHeroReveal and SplitExplanation across 16:9, 9:16 and 1:1. This branch extends the same source-pinned native evidence workflow to three additional MetricEvidence cases (nine total). Until the exact PR SHA finishes successfully, MetricEvidence native verification remains pending. It must pass at the exact source SHA before these extra split outputs are considered technically verified; even passing these checks does not constitute aesthetic approval or human acceptance. Each successful case produces:

- `hero.motionwright/manifest.json`: an actual portable project bundle, not a loose scene mock. In **Deliver → Inspect and import**, select the absolute path of the `hero.motionwright` directory, inspect it, then import the verified bundle.
- `motionwright-master.mp4`: a real six-second H.264/AAC master produced through Semwright's Motion Canvas and MLT providers. Its audio is deterministic **synthetic 440/660 Hz stereo test tones** for decoded H.264/AAC transport verification, **not sound design**.
- `native-contact-sheet.png`, sampled PNGs, onion and difference images, native layout inspection, and immutable source/evidence manifests. These are generated from native renderer frames, never substituted with the editorial SVG preview.

Import preserves internal scene/component identities and authored values while creating a fresh project generation. Existing native render readback tokens are not included in the portable bundle. Generate a new preview before using native inspection in the restored desktop project. Import refuses an already-existing project ID rather than overwriting it.

Open **Production → Components** and select the imported scene. The **Composition family** selector switches among a product reveal, a two-part explanatory graphic and an authored MetricEvidence study. The metric uses a dominant typographic fact, explanatory context and a permanent unverified-source disclosure; it never fabricates a data source or elevates a claim to verified evidence. In landscape the second family uses two columns separated by an intentional vertical rule; portrait and square reflow to stacked sections. Its six object identities remain stable, permitting per-property edits and conflict-aware family changes without flattening the project. A layout switch is an authored project revision only when explicitly saved, not when changing the preview. The existing default is preserved for old projects with no layout field. Change one bounded copy field or the accent, compare aspects, scrub the shared playhead, and update the component. Use **Edit objects** for ordinary object editing. Compatible human overrides survive subsequent component updates; conflicting copy, ambiguous reflow or a deleted component object produces an explicit error instead of regeneration.

In **Scoped changes**, queue edits with a reason, inspect the editorial A/B, then apply once. The reversible history can preview undo or redo. Every inverse is a new CAS-protected revision. An intervening conflicting edit, property lock, missing object or incompatible new scene duration blocks the inverse. The original journal is never rewound.

In **Native inspection**, select the output profile matching a current native receipt. Read at most eight sampled source frames, inspect an onion/difference view and record a frame-linked manual finding. A finding contains a source PNG digest, time, scene/object, constraint, severity, confidence and proposed local repair. The native frame grant is not written into the finding. Missing or stale native evidence is an explicit unavailable state. The optional **Draft scoped repair** action now transfers a selected native-frame digest and human repair note into the existing Scoped changes editor without generating replacement text, changing the project or bypassing preview/commit. See [Native review to scoped repair](NATIVE_REVIEW_TO_SCOPED_REPAIR.md).

## Data and migration contracts

Format 1 is read without changing revision or generation. Its first successful edit writes format 2 in the same existing transaction. A failed operation does not upgrade or alter the stored project. New creative state cannot be labeled format 1, and unknown future formats are rejected. The UI exposes the upgrade boundary before editing. This is not an implementation of cross-product legacy import.

`ProductionDesign` participates in branch capture, checkout and three-way merge. It contains an optional plan, component instances, native source capsules and a bounded reversible patch window. Plan approval binds to the current content digest and is a concept decision, not evidence that all claims, rights or renderer behavior are verified.

A native capsule is an immutable source asset reference plus declared fidelity metadata. It does **not** execute imported source, attest a third-party renderer, grant installation permissions, or make an opaque project parametrically editable. Metadata describing native/translated/baked/approximate/unavailable support is not an independent fidelity test.

Reversible patch records retain only their latest 64 entries, with a 256 KiB single-record and 4 MiB window budget. The ordinary application event journal remains complete. Large proposals are rejected explicitly when they exceed the byte contract. Arrays of authored keyframes are conservatively treated as one conflicting field during inverse merging; this is not arbitrary keyframe conflict resolution.

## Native motion and typography boundary

The preexisting exact linear XY profile and static camera pan were reconciled from `d848657ed78ba73a2c925c7c3eceecce8e4f9054`; they were not replaced or reimplemented as v0.5 work. New component entrances use synchronized Y/opacity keys with optional rotation and an exact out-cubic realization. They enter the canonical Film pipeline as SlideIn or Settle/FadeIn primitives. Other authored curves, scale changes, masks, blend modes, cross-shot continuity and camera rigs are not silently approximated.

The pinned native runtime attests Instrument Sans weights 400/500/600/700. Other weights are rejected before rendering, not rounded to a nearby face. Font files are not vendored in this implementation package. The editorial SVG viewer has an explicit system fallback and is not a native glyph-layout measurement.

Native component typography uses fixed non-flow containers and explicit authored line children. This prevents the provider's root layout from moving the text away from its editable position. Glyph measurement bounds and editorial line advance are separate so the provider's clipping check remains meaningful. Source scene objects keep their IDs; generated native line IDs are deterministic descendants, not new application objects.

## Evidence and acceptance

`creative-conformance.yml` runs the current Rust implementation to produce fixtures, then compares Studio's identities, every generated property, three-way merging and plan digests, including all three aspects of MetricEvidence. Checked-in fixtures are not used as an invented oracle.

`product-hero-e2e.yml` builds the exact pinned runtime on remote disposable runners, renders three aspects for each of the three authored composition families (nine cases), verifies each native frame/AV artifact, inspects actual color occupancy inside the independently realized source regions, and tests the spatial gate against synthetic negative cases. Source generation/revision and both repository SHAs remain explicit. A spatial occupancy check catches relocated or missing shapes/text, but does not prove exact per-glyph equivalence, accessibility, aesthetic quality or sound intelligibility.

A prior native test passed transport and font checks while text was being automatically repositioned. That result was not promoted to visual acceptance. The fixed-container projection and source-region regression gate address that discovered failure; the earlier failure evidence remains in its own immutable CI run.

Human design approval is still required. The automated ten-revision tests exercise preservation/restart and conflict behavior; they are **not ten real human requests**, a competitor benchmark or a user-preference study.

## Still outside this implemented slice

Actual HyperFrames Broker/Driver execution and direct-vs-mediated equivalence, experimental fframes evaluation, the remaining component library, BrandProfile/TasteProfile governance, real app-capture scenarios, ProductStage/3D cinematography, semantic SFX/ducking/mix production, external-project relink/OTIO import, paid generation, dynamic code/skill admission and competitive human pilots remain open work. The existing handoff and Platform boundaries are retained, not declared complete by adding new UI.

See `OWNERSHIP.md` for integration boundaries and `DESIGN_REVIEW.md` for the design-test protocol.
