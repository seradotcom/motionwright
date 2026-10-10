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

## Property-level fidelity and advisory skills (v0.5)

`crates/creative-library/src/fidelity.rs` audits each authored property group
against seven target routes, distinguishing `native`, `translated`, `baked`,
`approximated` and `unavailable`. These labels are tied to the implemented
semantic mapping, not a claim about output quality. For example, a chart may
retain editable vector bars in HyperFrames while the original numeric table is
**baked** because the typed data source is not yet a persisted first-class
project object. Cross-provider targets without an implemented projection remain
`unavailable`, even if their source files can be attached as opaque material.
These reports expressly set runtime verification and creative approval false.

The twelve authored v0.5 knowledge skills are `direction`, `causal-story`,
`typography`, `motion`, `product-stage`, `capture`, `sound`, `responsive`,
`critic`, `repair`, `distillation`, and `delivery`.
`crates/creative-library/src/skills.rs` produces source-digest-bound
**advisory preflight findings** and checks mechanical facts it can establish:
minimum type size, synthetic data classification, authorized asset presence and
missing temporal/approval evidence. They remain **knowledge-only, not installed**
executors. No skill may grant an execution capability, rewrite a locked object
or publish on its own; every assessment records an explicit evidence limit.

`crates/service/tests/native_creative_revision_sequence.rs` is a separate
persistent SQLite acceptance lane: ten successful edits and ten stale-write
rejections, with reopens between changes and a preserved human content lock.
It demonstrates the persistence/CAS boundary, **not** independent human
creative review or a measured speed advantage.

## Original 3D stage prototype (direct runtime, not Broker-ready)

`runtime/blender-stage/fixed_render.py` consumes a source-validated first-party
typed `StagePlan`, not arbitrary project Python. The direct CI lane must open
actual Blender, save a fully editable `.blend`, generate a GLB interchange,
and produce a few honest native camera-motion preview frames. Production
source stays full resolution; the sampled preview runs at half resolution.
The explicit renderer profile is **Eevee** to avoid the Ubuntu distro Blender
Cycles build's unavailable OpenImageDenoise dependency. This does not claim
Cycles equivalence or a mastered full-length scene. GLB loses certain Blender
AREA light semantics, which the manifest must disclose; the editable Blender
source remains authoritative. The direct script is not permission for an
untrusted agent to execute arbitrary Blender Python. Final Broker/Host
admission remains a separate required gate.

## Source handoff and review boundaries

A technical contact sheet, native-readback manifest, draft reference and
sound PCM artifact do **not** constitute a production-ready campaign. Owners
must review each target format, exact source, licensed materials, accessibility,
readability and temporal continuity. Committing a recipe's typed native HTML
source through Studio is one revision, never an implicit runtime grant.

## Rational sound events and five-frame native review

The first-party `CreativeClockMap` in `crates/creative-library/src/clock.rs`
supports timeline, voice, music and source-media clocks as monotonic exact
integer anchors. It preserves sub-sample fractions rather than accumulating
rounded frame durations; an event that lands between PCM samples is refused
until a separately authorized resampling/retiming choice is made. Semantic
focus and transition cues can place their original synthetic sound into an
explicitly bounded 48 kHz soundtrack only when both the exact source revision
and human cue approval match. This remains an authoring plan: narrator/track
mixing and master loudness acceptance are separate.

`NativeHtmlContactReview` in Studio reads at most five source-verified native
PNG frames through server-owned, project/revision-scoped grants. It supports
onion and bounded RGB differences and revokes temporary browser blob URLs.
The comparison is deliberately a thumbnail inspection, not a full-frame
bit-equivalence or artistic-quality verdict. A reviewer must still inspect
complete temporal transitions and source/target dependencies.

The isolated HyperFrames runner also records a bounded fixed set of technical
milestones and categorized browser-start failures. These logs contain no user
copy, credentials, arbitrary stdout/stderr or external URLs; they help identify
whether an owner-constrained browser fails at module loading, namespace
creation or resource limits without relaxing the existing Host sandbox.

## Explicit component-instance overrides

`crates/creative-library/src/instance.rs` provides typed, atomic, nonmutating
instance-override proposals for copy, brand color, motion character/opt-in,
procedural seed and authorized media substitution. Each change specifies the
previous value or asset digest, retains a stable component UUID and checks the
previous canonical request/source SHA. No override modifies the shared
first-party recipe definition. A duplicate field or stale edit is refused,
rather than overwriting later human work. Each proposal explicitly requires
the existing project source compare-and-swap edit. This is an **authoring
contract**, not a claim that independent, editable template-to-instance rebase
has already completed the full production persistence and UX cycle.

## Blender complete native temporal sample

The original Blender 4.x product stage has a separate bounded full-clip
acceptance implemented by `runtime/blender-stage/render_clip.py`. The
disposable CI fixture generates 90 real sequential frames from the exact
previously saved/reopened editable `.blend`, verifies SHA per frame, and
creates a verified 30 fps H.264 MP4 review copy. It covers one of four camera
recipes (`arc-reveal`) and **has no audio and no mastered production claims**.
Other 3D recipes remain sampled previews. A .blend retained at original output
resolution, its GLB interchange and the source-bound loss report accompany
the captured example; the MP4 review copy does not replace editable source.

## Chromium's sandbox and the canonical Host

The v2 native profile distinguishes two **fail-closed** confinement modes.
Direct native execution requires the browser's built-in Chromium user-namespace
sandbox. Inside an already enforced and unprivileged **Semwright Driver Host
Bubblewrap/AppArmor** environment, Chromium would otherwise try to create a
second namespace explicitly denied by the strict `bwrap-userns-restrict`
profile. Only when the process itself has **all** of these independently
observable properties does the capture runner select the existing outer
Bubblewrap boundary instead: a one-UID non-root-to-root namespace map,
`NoNewPrivs: 1`, zero effective capabilities, an enforced AppArmor label
containing `bwrap`, and the host's unprivileged namespace restriction still
enabled. No environment variable or project-source argument can opt into this
mode. The exact choice is retained in the frame manifest and the verified
runtime result. If any proof is missing, the runner requires the browser's
own sandbox and fails if it cannot start. The model can never select
`--no-sandbox` or launch a browser with arbitrary arguments.

This explicitly **does not lower the Semwright Host boundary**: the host
still mediates sealed tools, read-only runtime/source mounts, a writable
attempt-specific workspace, a network namespace without external access,
bounded resources, and its own session/grant authority. This is a limitation
of nested namespace enforcement, not an automatic fallback to an
unconfined browser. Human acceptance and an external security review
remain pending. Do not disable AppArmor or broaden host filesystem/network
grants to make the test pass.

## Explicit localized repair contracts

`crates/creative-library/src/repair.rs` proposes one to three typed adjustments
to a retained native document: bounded pose/opacity/size, effect blur, exact
text runs and exact rational keyframe **values**. Every repair validates the
currently observed source SHA-256 and the expected original property value.
No source code, unbounded effect, arbitrary script, path or renderer privilege
is accepted. The library refuses changes to locked human fields and properties,
any repeated field within one atomic proposal, nonexistent nodes, stale
keyframe knots, no-op edits, non-finite values and invalid native documents.
A proposal retains all other objects and original source data, carries
conservative full-scene invalidation until the renderer proves a narrower
temporal dependency, and sets execution/equivalence/approval to **false**.

The desktop `native_localized_repair_preflight` compares this proposal
against the canonical domain's `CreativeWorkspaceEdit` validation before
returning a proposed edit and an actual source-level difference. Studio's
`NativeRepairWorkbench` offers a bounded one-field repair, preview and
**separate user-driven commit** through the existing project revision CAS.
A browser demo cannot pretend to approve or perform a native repair.
This is not autonomous pixel correction: the owner must inspect new native
frames and accept/reject the resulting design.

## Exact-SHA design-review archive for human sessions

The CI-only `creative-human-review-pack` job downloads original synthetic
artifacts from the **same exact Motionwright commit** after three native
visual review shards, Blender preview/editability and original PCM
measurements have passed independently. The zip contains source-bound
contact sheets, a retained editable Blender source/interchange, a 90-frame
video review copy, three deterministic original sound WAVs, signal
measurements and a blank human scoring sheet. Every included file is
SHA-256 listed and named relative to a small closed set; no private
owner content, executable browser, installed npm tree or licensor fonts
are bundled. The archive is not created if any required upstream
technical result is missing, untrusted or describes a different source
revision. The `canonical-broker` execution gate remains separate and may
still be red while synthetic design review materials are available.

Human reviewers should record reasons for approval or rejection in
`review-sheet-template.json`; scores default to **null**, not a synthetic
rating. Required categories cover story, composition, typography,
timing, camera, direction, provenance, editor retention, audio, formats
and parity against a competent direct-renderer agent. Source-bound
technical artifacts do not certify craft or commercial publication.


## Selective expensive CI verification

The source-bound workflow still runs all full gates on each explicit
development-branch push or relevant PR. To avoid repeatedly installing
Chromium, Node, Blender and Semwright for every narrow debugging edit,
the owner can dispatch one **selective** gate on the same immutable SHA:

    gh workflow run hyperframes-native.yml --ref ci/v05-native-fidelity-continuation -f selected_gate=broker

Other dispatch values are `full`, `creative-library`, and
`creative-design`. The `creative-design` gate keeps three visual
shards, native Blender, original PCM and the combined source-attested
human review ZIP together; `broker` runs only the canonical real Host
gate. Selective gates do not certify a new release, override red full
gates or modify runtime grants. Release review still requires the
**full same-SHA matrix** and independent security/creative approval.


## v0.5 delta requirement audit, separate from legacy acceptance

`docs/creative-production/V05_DELTA_REQUIREMENTS.json` preserves the
**64 numbered additions/deepening tasks and 16 epics** from the supplied
v0.5 SRS delta, but never treats source presence as release acceptance.
The isolated branch inventory currently records 37 **PARTIAL code
candidates**, 1 **BLOCKED** canonical Broker/HyperFrames integration,
and 26 **NOT_VERIFIED** requirements. These are conservative per-ID
bookkeeping states, not completion percentages, and do not replace the
required reconciliation against the **208 original product IDs and 60
acceptance tests** assigned to the legacy-product workstream. All 64
retain `release_acceptance: NOT_CLOSED` and
`human_review: NOT_PERFORMED` until a same-SHA acceptance record is
linked. The Python contract tests reject any absent source path or
implicit promotion from a Rust file to an approved product claim.


## Distillation: positive+negative evidence does not create install trust

`crates/creative-library/src/distillation.rs` now provides a *source-only*
`DistillationDraft` and `experiment_source_variants` contract to start
`MW05-E05-04`. The draft requires at least two independently digest-bound
positive and two negative example studies (maximum 32), owner-attested rights
for every source, explicit strengths/failures and an unchanged Brand/Taste/
ComponentRequest fingerprint. The experiment produces nine distinct typed
sources (16:9, 9:16, 1:1 by English/Spanish/German), rejects duplicated
instance identities, unreviewed template edits and silently reshaped
formats, and reports each original source SHA without claiming renderer
execution. Its result explicitly records `NOT_PERFORMED` pixel verification,
`NOT_REVIEWED` human quality, and `NOT_REQUESTED` owner installation.
It cannot install a plugin, execute imported source code, promote a recipe
or grant an agent rights. A future owner-verified disposable-code admission
procedure is still required for `MW05-E13-02` and is **NOT VERIFIED**.

### Desktop source-distillation trial (read-only)

`Production → Creative library → Source distillation` now includes an
advanced, locally hosted review of **four user-referenced design sources**
(two positive and two negative). The input uses their independently supplied
source SHA-256s, explicit owner rights declarations and concrete
strength/failure observations. The desktop validates that every image/font
used by the base recipe and its nine translated layout variations already
exists in the exact current project by ID and digest. The service accepts
only the current project revision, builds the nine designs on a bounded
worker and checks revision again afterward. It exposes per-format native
source digests, no images/videos and no installation privileges.

The Studio interface intentionally has **no Promote/Install button**:
the experiment is a draft proposal, not a renderer test or a trusted plugin.
This does not yet fulfill optional arbitrary component code import/admission,
positive/negative **independent human** vetting, multi-variant rendered
comparison or published content in `MW05-E05-04`/`MW05-E13-02`.
It adds a working source-experiment step rather than inflating counts of
approved creative components.


## Competent renderer comparative protocol (E09-04)

A first-party, source-hashed baseline comparison protocol is now available in `tooling/creative-benchmark/`. It validates identical brief, source assets, budget and ten edit requests across Motionwright through canonical Semwright Host and a competent direct HyperFrames/Blender arm. Editable project sources, actual rendered revisions, exact SHA, cost, time, lock outcomes and blind human ratings are tracked without auto-selecting a winner. CI only exercises synthetic contract fixtures and publishes a `NOT_RUN` protocol; all real comparison results remain unverified. See `V05_COMPETENT_DIRECT_BENCHMARK.md`.


## Immutable Launchwright content handoff candidate (E14-04)

The existing project `HandoffBinding` can now be used by `tooling/launchwright-handoff/export_handoff.py` to package the exact authored Project snapshot and final media master byte-for-byte. It requires an exact project revision, master SHA, source-bound ProductionPlan approval, verified rights/claims and no publication request. The ZIP records the candidate source identity and leaves Launchwright import/publish, final decode and reviewer authentication to their independent authorities. CI runs synthetic-only tests, not a live Launchwright transfer. See `V05_LAUNCHWRIGHT_IMMUTABLE_HANDOFF.md`.


## Dirty-frame invalidation and measurable transfer vs actual render work

`crates/creative-library/src/dirty.rs` provides a conservative **source-only**
proposal under `MW05-E03-03`. It compares before and after fully validated
native HTML documents, retaining exact source SHA, rational opacity HOLD
bounds and stable node identity. Global source/camera/asset/canvas/order
changes dirty every frame; interpolated opacity, unknown temporal semantics
and visible edits cannot be incorrectly marked reusable. Only a rigorously
invisible zero-opacity node (including inherited parent opacity) or a pure
metadata lock/name change may suggest pixel reuse, and only in the
**original native HTML visual** profile. All observation readback, sound
samples and media encodes are marked not reusable, even when a frame's
source-level pixel proposal appears unchanged. Studio presents proposed
ranges as unverified; a user must render and inspect again.

`tooling/hyperframes/native_dirty_e2e.py` provides a disposable CI oracle:
render 90 before + 90 after **real native browser frames**, compare all
reusable frame SHA-256s, reconstruct all 90 after frames by copying old PNGs
for the provably invisible source interval, and compare the reconstructed
sequence/FFV1 decode against the full-after result. It separately measures
source comparison, actual frame-render time, FFV1 encode time and a smaller
SHA-verified dirty-PNG transfer ZIP. This **does not claim saved render,
compilation, encoding or transfer work beyond actual measured PNG bytes**.
The production runtime still renders all frames on this acceptance fixture.
Spring/lookbehind, shutter, transitions and audio-tail dependency-aware
incremental recomposition remain release blockers for the broader E03
requirement. This distinction is enforced by fields stating
`actual_native_frames_avoided: 0` and
`rendered_pixel_equivalence_verified: false` in any preflight proposal.


### Independent dirty-transfer receiver

The CI-equivalent patch ZIP now has a companion explicit
`transfer-source.json` with old/new source digests, ordered dirty and
reused intervals, and every expected native PNG SHA-256. The independent
`tooling/hyperframes/verify_dirty_transfer.py` refuses symlinked caches,
out-of-order/overlapping intervals, forged receipts, unknown executable ZIP
entries, altered owner sources and reused-frame hash drift. It materializes
one new complete 90-frame output only after checking the actual bytes from
the old source cache **and** the actual dirty PNGs received in the patch
archive; it never reads the new full-after input during reconstruction.
The full-after actual Chromium PNG and FFV1 are separate validation oracles.
Transfer savings account for the receipt bytes as well as the patch ZIP,
whereas native render frames avoided remain **zero** on this fixture.
No production file-transfer scheduler, audio-aware dependency cache or
human-approved release is implied.

### Source-bound measured acceptance (2026-10-10)

GitHub Actions run `38037146221`, exact SHA
`ca4a089ea8aebdbe3a6f7c8fdf3620c095d95268`, verified 90/90
received PNG SHA-256s and a full-after FFV1 decode equivalence, using a
purely independent receiver operating on old cached frames plus new
dirty-frame ZIP bytes. Full-after transfer was **786,172 bytes**; the
60-frame patch was 545,662 bytes, and patch **plus** separate receipt
was 552,786 bytes. The verified net transfer reduction was **233,386
bytes** (29.7% for this deliberately simple synthetic scene). The
receiver does not claim to have saved source readback, encoding, audio
or Chromium work: the acceptance rendered 180 frames in full, so actual
rendered frames avoided were **0**. E03-03 and E03-04 remain **PARTIAL**,
not release-accepted; this result proves custody and candidate visibility
windows, not a finished incremental production scheduler.


## Source-window native execution prototype (real frames omitted)

The existing first-party `motionwright-hyperframes-runner` has an additional
**direct native** `render-window` subcommand with fixed typed
`--first-frame` and `--end-frame-exclusive` integer arguments. This does
not add a second HTML renderer, arbitrary source executor, caching backend or
new Semwright Host manifest grant. The pinned runtime, browser signature,
source SHA, project/generation/revision, PNG hash and owner-supplied roots are
the same as for `render`. A range outside the exact source timeline is
refused **before any renderer launch**. The renderer genuinely seeks/screenshots
only frames in the requested range, and writes a distinct
`motionwright.hyperframes-native-frame-window/1` receipt with
`coverage=selected_window`, absolute frame indices and independent
window time readback. It has **no FFV1 master output**, no outside-range
observations and cannot claim source cache permission.

The disposable CI acceptance renders 90 first-source frames and all 90
changed-source frames as the independent native oracle, **plus** the bounded
60-frame changed-source window. The 30 previously sourced PNGs are
reassembled with the 60 actually rendered window PNGs by the same strict
independent receiver described above. Both the complete 90-frame PNG list
and decoded FFV1 must be identical to the independent full-after source.
The partial capture can truthfully report 30 native screenshot/evaluation
calls *omitted relative to a full after render*. The **combined acceptance
test still renders 240 frames**, so it cannot claim 30 net saved frames or
a robust production time advantage from this test. Encoding, media
decoding, sound, motion blur, transitions and owner-granted cache lifetime
are separate unresolved concerns. Only a future canonical service/Host
adapter with authenticated cache grants could elevate this prototype to
production incremental rendering.


## Narration source replacement and dependency impact (E08-02, partial)

`crates/creative-library/src/narration.rs` works exclusively with the
**existing** `Project.audio` domain, not a parallel transcript or clock.
It inspects the project's active measured voice track, exact SHA-256-linked
audio asset, stored text-aligned `TranscriptSegment`s and associated
`AudioCue`s. The resulting source snapshot contains the immutable
project ID/generation/revision and an exact digest of the current voice,
words and cues; its owner/authentication and media-decode claims remain
`false`. Manual or sufficiently confident measured timing evidence can
be identified; unknown or low-confidence ASR produces the explicit
`REQUIRES_MANUAL_TIMING_REVIEW` classification, never an approved
transcription.

Given a *subsequent persisted project revision*, the source comparison
rejects stale/cross-project lock references, forged source bytes and
unrelated changes, then reports altered segment/cue IDs and rational
time spans. Captions must regenerate and undergo human review, cuts/B-roll
must be re-evaluated, and a replaced measured voice asset requires new
mix measurements. **No rendering, timeline mutation or automatic
owner lock is performed**: a real locked take must still be authenticated
through the canonical project service and edited by a separately accepted
new revision. This is the source/impact half of E08-02, not the
end-to-end accepted narrative lock or an ASR truth engine.


### Desktop audio narration preflight

The existing `AudioWorkspace` now surfaces `NarrationTakeReview`, a
**read-only** source comparison. It can snapshot the *measured active take*
from the exact current project revision and, after a separately committed
editorial change, compare it with the newer exact revision. Every request
is checked through the canonical StudioService scope and the older
SHA-256-bound source snapshot; work runs on a bounded desktop worker
and the service re-checks revision before returning the impact.
The browser demo refuses to invent original measured voice.
The response identifies changed segment IDs, cue IDs, rational time
spans and necessary caption/cut/B-roll/mix reviews; it never mutates
the project, processes a new voice provider or claims actual owner
authentication. The baseline is ephemeral UI state to avoid quietly
persisting a purported owner-signed take. A true owner-approved
persistent narration lock and mix/caption reflow must still be
implemented as a separate canonical change operation.


## Real-media OpenTimelineIO interchange candidate (E11-03)

The existing Rust `otio_interchange` remains the canonical source for a
conservative linear Timeline. `tooling/otio-media/bridge.py` now extends
that output **only after independent video byte readback, SHA-256 asset
ledger agreement, owner/redistribution rights and ffprobe source frame
checks**. It packages a portable relative `ExternalReference.1` per
explicitly bound original media scene, while leaving unbound abstract
scenes as `MissingReference.1` without inventing filler media. The
original editable Project JSON, per-property loss report and exact
media bytes remain available together in an immutable ZIP.

An independent bounded import reader recognizes reordered original
source clips and exact source-rate end trims as proposed
`MoveScene` / `SetSceneDuration` changes, rejects in-point changes
that lack native representation, newly invented media, source drift,
stale project snapshots and arbitrary NLE effects. It **does not**
commit project revisions or self-authorize imported media. A
GitHub-hosted native acceptance compiles the existing Rust exporter,
generates a real H.264 test video and opens the portable OTIO through
the independent OpenTimelineIO Python reader, with real ffprobe
readback. This is *not yet* a Kdenlive/Shotcut visual round-trip or
a human approved editorial cut; see `V05_OTIO_MEDIA_INTERCHANGE.md`.


## Independently admitted technical observations (E13-04; owner trial only)

A new `tooling/observer-admission/` offline gate accepts only one admitted
first-party PNG dimension method (`png_dimensions_v1`). Exact verifier
source bytes, declared method/version/units/coverage/limitations and an
owner's active/revoked policy are separately Ed25519-signed; a distinct
observer key signs one source-bound producer result. The actual original PNG
bytes are independently decoded and checked by the gate. A separately
owner-provisioned **current policy head** outside the evidence bundle pins
its generation and exact canonical signed policy envelope so that a
previously signed, now-revoked policy cannot be replayed. Revoked evidence
is retained as a historical signed record but is no longer accepted as
a *new* gate result. Owner authentication, production key custody and
Semwright Platform/Native SDK integration are still **NOT VERIFIED**.
This experiment cannot execute imported verifier source, install a driver,
approve art or authorize publication. See `V05_OBSERVER_ADMISSION.md`.
