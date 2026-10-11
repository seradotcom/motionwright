# Native production boundary

Motionwright owns creative project state, revisions, branches and local production receipts. It does not own Semwright execution authority.

## Owner-provisioned connection

The desktop runtime accepts an explicit motionwright-semwright-connection/1 file containing an absolute Semwright CLI path, its SHA-256 digest, broker socket, private session file, output root and the exact Motionwright resource bound to that workspace. Paths are revalidated before each call, symlinks and group/world-writable connection paths are rejected on Unix, and the CLI digest must still match.

The connection is not a generic command tunnel. Motionwright enables a closed production command set for Motion Canvas composition/render operations and MLT frame encoding, sync probing, AV muxing and render job operations. Broker output must be a typed Semwright envelope with Driver provenance, the expected provider identity, a descriptor SHA-256 and a provider generation.

The bounded [cross-app selective recovery demonstration](reliability/cross-app-selective-recovery.md)
reuses a verified Semwright Blender GLB across fresh Motionwright processes
before the separate Motion Canvas render. It keeps unresolved native outcomes
blocked instead of rerunning mutations; exact-source multi-provider CI is an
independent verification gate. It is not a complete generic workflow scheduler.

## Receipts and retry safety

Production receipts are application-owned history, not execution authority. Each row is anchored to:

- Motionwright project ID, generation and revision;
- caller request ID;
- SHA-256 of the exact command and JSON arguments;
- production stage and bounded response/error metadata.

A request ID cannot be reused with different input. A completed request replays its stored result instead of dispatching again. A request whose mutation outcome is unknown fails closed and must be inspected before any retry. Late results remain attached to their original project revision and never silently become current after creative edits.

**Provider-generation loss after a started render:** a failed read-only `render.status` call does not prove that its previously acknowledged rendering job failed. Motionwright now preserves the *render operation* as `outcome_known=false`, retains acknowledged-start progress and refuses automatic re-render/re-provisioning. The local Jobs projection treats a failed status observation as `OutcomeUnknown` for a nonterminal job. See the exact-SHA [provider loss finding and reconciliation procedure](reliability/provider-generation-loss.md); upstream Driver Host lifecycle investigation remains open as issue #49.

## Native Motion Canvas segment production

The desktop Deliver surface exposes a bounded production operation for saved delivery profiles across landscape, portrait and square output. Width, height and canonical frame rate come from the versioned profile; narrative role plus motion archetype remain explicit for every scene assigned to Motion Canvas. Alternate aspects default to semantic replan. Crop is accepted only when the profile records explicit approval. The exact Semwright pin bundles and permits `Instrument Sans Variable` plus `IBM Plex Mono`; Motionwright exposes those identities as read-only production facts rather than pretending arbitrary local fonts are supported. The project Visual language must also carry explicit `ink`/`text` and `surface`/`background` palette tokens. New projects receive conservative ink/surface defaults, while a project that explicitly removes those tokens fails before dispatch. The UI does not infer missing creative semantics at render time.

Before starting expensive production, the operator may run a [read-only Film semantic preflight](film-preflight.md). It uses the same canonical projection from the exact application revision without dispatching a job or claiming renderer success.

The coordinator projects only contiguous Motion Canvas scenes into the pinned canonical Film contract, maps the versioned visual-language tokens into the Film editorial profile, then executes `composition.plan`, `composition.apply`, `render.start`, bounded `render.status` polling, `render.result` and `composition.verify` through the owner-provisioned Semwright connection. The plan reference, applied source fingerprint, render job reference, artifact frame count and native verification report must all agree with the same Motionwright generation/revision. Verification is accepted only when Semwright reports completed native support, zero findings and a non-empty all-PASS validation check set.

This operation intentionally produces verified Motion Canvas segment artifacts, **not** a generic promise that every configured master format can be emitted. Desktop operators can subsequently initiate the narrowly certified [single-segment MLT H.264/AAC MP4 master](desktop-av-master.md) using an already-bound, measured 48 kHz stereo WAV from project storage; other renderers and multi-segment AV mastering remain separate gates. The [read-only multi-segment MLT preflight](multi-segment-preflight.md) checks canonical Film segment identity/order, exact-frame cut offsets and SHA-256-verified manifest bytes for eligible landscape cuts. A separate internal, [source-bound MLT edit recipe/FFV1 preparation stage](mlt-multi-segment-preparation.md) can invoke the pinned MLT frames encoder for each verified segment; neither operation alone creates a finished MP4. A separate [experimental owner-only native MLT timeline runner](mlt-native-timeline.md) now constructs actual semantic video clips and can produce a digest-verified video-only FFV1, but it remains unexposed until a dedicated two-segment Driver Host E2E proves it; H.264/AAC audio mastering is still a separate gate. [Source-verified native PNG preview](native-frame-preview.md) permits bounded, session-authorized readback of actual renderer frames without claiming a real-time AV player. The direct pinned MLT audiovisual master is currently certified only for H.264/AAC, 48 kHz, Rec.709, MP4 and checks those versioned profile fields plus exact frame-rate agreement before mux. Other configured combinations fail as unsupported instead of being silently converted. Native Film admits a tightly bounded [linear X/Y position settle](native-linear-position-motion.md) and an [exact 0→1 linear opacity FadeIn](native-linear-opacity-motion.md), each with source-owned rational timing and pinned Native SDK mapping. General curves and all unsupported channels still fail closed rather than being approximated. The pinned native render CI exercises actual moving PNG frames and an independently decoded opacity ROI, not a fake static composition.

`.github/workflows/native-render-e2e.yml` is the heavy exact-SHA acceptance lane for this boundary. It provisions the pinned Semwright Broker/Driver Host on an ephemeral GitHub-hosted runner, installs the pinned Motion Canvas runtime and Firefox, keeps network disabled for the driver, renders a two-second application-owned revision to 60 real frames, verifies the driver artifact manifest and retains first/middle/last review frames plus machine-readable evidence. A written workflow is not a PASS; the evidence belongs to the exact Motionwright SHA only after that run succeeds.

## Authority boundary

A valid local receipt means only that Motionwright recorded a Broker interaction. It does not imply render quality, Project Graph admission, Effect Conformance PASS, creative approval or freshness. Those claims require their canonical Semwright evidence paths.

## Job correlation projection

Motionwright does not persist a second scheduler or a competing runtime state machine. The Studio derives a bounded job ledger from its immutable production receipts and groups only the allowlisted Motion Canvas and MLT render job commands.

The projection keeps execution state separate from result applicability. CANCEL_REQUESTED is never rendered as CANCELLED until the canonical driver confirms cancellation. A definitive SUCCEEDED, FAILED or CANCELLED observation cannot be resurrected by a later incompatible active-state receipt. An OUTCOME_UNKNOWN observation may be reconciled by a later canonical observation. The original project generation/revision remains the applicability base, so a late successful result is displayed as STALE after the creative project advances.

Only safe correlation metadata is copied into dispatch/error receipts for job commands: the job reference when one already exists. Render arguments, private logs, session contents and arbitrary filesystem paths are not added to the job projection. Progress and artifact indicators appear only when the canonical driver actually returned them.

The same derived ledger is available to the desktop UI and through the Semwright Native SDK production-jobs observation scope. That scope exposes Motionwright-owned receipt history; it does not create runtime authority. The visible-workspace reread policy, manual refresh semantics and failure boundaries are described in [local receipt rechecks](production-receipt-refresh.md).
