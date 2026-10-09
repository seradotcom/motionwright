# Motionwright v0.5 — Native creative library, state and operating contract

> Development document, not a release note. The named components below are **candidates** until their own
> renderer-native fidelity, rights, editability, source-provenance and independent human review gates have passed.

## Grounding and identity

- Product repository: `seradotcom/motionwright`. The first-party library is
  `crates/creative-library`, the typed-native HTML boundary is `crates/hyperframes-profile`,
  and the canonical Semwright Host provider is `crates/driver-hyperframes`.
- The version used by the actual HTML renderer is **HyperFrames Core 0.8.143**, with GSAP **3.15.0**
  and Playwright **1.55.1**. The profile's installed runtime fingerprint includes the npm lock,
  assets, selected Chromium executable, font dependencies and full immutable file inventory.
- These identities are **runtime pins**, not rights grants. The exact installed module tree must
  be owner-approved; a network URL or pasted JavaScript never becomes a native model-command capability.
- Source assets remain SHA-256 bound. The importing project must contain each referenced asset
  by matching identifier **and digest**. Rights declarations are owner statements, not externally
  verified licenses. Attribution and terms must survive export.

## Native source and creative model

The domain persists `ProductionDesign.workspace.native_scenes`, scoped to one existing
scene/deliverable pair, through the existing revisioned Studio service and Native SDK. It does not
replace the project with an HTML document or lower nonrepresentable source semantics into Film.

The typed native document admits scene-level composition, parent groups, vector shapes,
rich text runs, digest-bound images/video/font assets, blending, masks, camera and explicit rational
subframe knots. Human locks protect values, curves and fields. Replacement requires the observed
source SHA and fails on locked edits. Revision stale/unknown outcomes remain visible, never silently
retried. Renderer execution is separate from observing, proposing, editing or reviewing source.

The catalog exposes **36 original candidate recipe identities** and six direction kits:
`editorial-precision`, `kinetic-statement`, `product-stage`, `material-study`,
`causal-lab`, `documentary-signal`. Each recipe declares its backend, purpose,
need for data or media, source classification and review status. A kit is not a global art style:
a user may make different choices, combine recipes or preserve incompatible native projects.

### Explicit support levels

| Component family | Authored source currently present | Technical gate still required |
|---|---|---|
| Type and layout | Native HTML layers, typography, responsive geometric proportions | Frame-level clipping, locale and human reading review |
| Product and software | Native HTML screen framing, plus original generic Blender stage plans | For real screens, owner-imported source; for Blender, actual Host-rendered scene and interchange |
| Transition and camera | Native HTML masks/continuity and editable Blender 3D camera paths | Complete source/target spatial continuity and full-frame readback |
| Data and procedural | Digest-bound numeric series with units, uncertainty, source and seeded original geometry | Correct axes, density, truth-in-copy, composition checks at output size |
| Sound | Editable original, seekable 48 kHz stereo synthetic score; RIFF PCM output with SHA | Mastering, voice intelligibility, synchronization, spectral/artistic review |
| External libraries | Catalog only when approved by owner via explicit integration | License, privileges, exact-version conformance |

A technical PASS asserts only the tested source/runtime/dimensions/frame invariants.
It does **not** assert artistic quality, comparative advantage, market fit or product truth.

## Using Studio

In `Production → Creative library`, select a real scene, output, kit, recipe, content, locale,
brand colors, taste, source assets and permissions. The first action creates a **proposal**, not
a render or a committed edit. It must pass native validation. The `normalized_edit` can then be
committed as **one revision** only for native HTML realizations; Blender and audio plans are
currently exposed as plans, not advertised as fully integrated Host exports.

In `Production → Native editor`, inspect persisted objects, curves, masks and protects. Rendering
requires explicit owner opt-in for the exact installed runtime SHA, selected renderer, current
project generation/revision and a render-local effect grant. The frame scrubber requests readback
by ephemeral server-issued frame token. It does not execute imported source inside Studio.

If a render reply is lost, use the **same logical attempt ID** to observe, cancel or recover the
recorded result. Never start a duplicate attempt implicitly.

## CI and native fidelity

`.github/workflows/hyperframes-native.yml` isolates separate gates:

- `creative-library-contracts`: catalog, 36 typed recipes, three aspect ratios, three locales,
  input/source determinism, rights refusal, typed numeric sources, waveform and WAV contracts.
- `creative-visual-review`: actual Chromium-rendered frames in three CI shards; every frame
  gets a hash, selected frames and a contact sheet are retained. A contact sheet helps a human
  review but is not an automated aesthetic PASS.
- `native-profile`: direct native engine, transparent/opaque pixels, 30000/1001 exact seeking,
  full-frame readbacks, repeatability and digest provenance.
- `creative-integration-contracts`: migration, revisioned persistence and retained native source.
- `native-studio-desktop`: React, unit/e2e browser refusal of fake native actions, Tauri command,
  asset/provenance and read-grant boundaries.
- `canonical-broker`: actual Semwright Broker, immutable pin, scoped Driver Host, AppArmor/bwrap,
  exact renderer-runtime SHA, comparison against **same competent direct renderer**, and recovery
  of the same logical attempt without job duplication.

### Security and unattended work

Heavy builds and rendering belong in disposable CI. Do not install native runtimes while merely
opening a project, lower local policy, disable AppArmor, execute a model-provided script,
grant networking by default, install from unpinned registries, or copy user content into evidence.
`tooling/hyperframes/owner_provision.py` emits a **review-only** manifest and owner-policy
template; it requires explicit rights/sandbox acknowledgments, validates the runtime and
never modifies the live Broker. Any later owner installation or policy change is separate.

## Product completion criteria

Before calling these recipes complete, publish same-SHA results for: all applicable recipe/aspect/
locale fixtures; actual Blender and audio integration, not just stage plans; media capture with
source provenance; edited-source roundtrips including locks and stale revision; real project
reopen/backup/restore; full voice-to-AV output; non-simulated campaign variants; performance and
accessibility in installed desktop; and **independent human art-direction review**. A counter
of 36 plan instances is not 36 approved designs. The SRS/W0–W7 production master remains the
completion authority; this file does not remove any of its later stages.
