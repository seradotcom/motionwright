# Native production boundary

Motionwright owns creative project state, revisions, branches and local production receipts. It does not own Semwright execution authority.

## Owner-provisioned connection

The desktop runtime accepts an explicit motionwright-semwright-connection/1 file containing an absolute Semwright CLI path, its SHA-256 digest, broker socket, private session file, output root and the exact Motionwright resource bound to that workspace. Paths are revalidated before each call, symlinks and group/world-writable connection paths are rejected on Unix, and the CLI digest must still match.

The connection is not a generic command tunnel. Motionwright enables a closed production command set for Motion Canvas composition/render operations and MLT frame encoding, sync probing, AV muxing and render job operations. Broker output must be a typed Semwright envelope with Driver provenance, the expected provider identity, a descriptor SHA-256 and a provider generation.

## Receipts and retry safety

Production receipts are application-owned history, not execution authority. Each row is anchored to:

- Motionwright project ID, generation and revision;
- caller request ID;
- SHA-256 of the exact command and JSON arguments;
- production stage and bounded response/error metadata.

A request ID cannot be reused with different input. A completed request replays its stored result instead of dispatching again. A request whose mutation outcome is unknown fails closed and must be inspected before any retry. Late results remain attached to their original project revision and never silently become current after creative edits.

## Native Motion Canvas segment production

The desktop Deliver surface exposes a bounded production operation for saved 16:9 profiles. The user supplies the canonical frame rate, primary/monospace fonts and an explicit narrative role plus motion archetype for every scene assigned to Motion Canvas. The UI does not infer those creative semantics at dispatch time.

The coordinator projects only contiguous Motion Canvas scenes into the pinned canonical Film contract, then executes `composition.plan`, `composition.apply`, `render.start`, bounded `render.status` polling, `render.result` and `composition.verify` through the owner-provisioned Semwright connection. The plan reference, applied source fingerprint, render job reference, artifact frame count and native verification report must all agree with the same Motionwright generation/revision. Verification is accepted only when Semwright reports completed native support, zero findings and a non-empty all-PASS validation check set.

This operation intentionally produces verified Motion Canvas segment artifacts, **not** the final audiovisual master. MLT assembly, final audio realization and AV mux remain separate production stages until they are connected to the same revision-bound path. Generic Canvas keyframes also remain fail-closed in canonical Film projection until the pinned Semwright authoring contract can preserve those curves exactly; Motionwright never substitutes an approximate animation silently.

`.github/workflows/native-render-e2e.yml` is the heavy exact-SHA acceptance lane for this boundary. It provisions the pinned Semwright Broker/Driver Host on an ephemeral GitHub-hosted runner, installs the pinned Motion Canvas runtime and Firefox, keeps network disabled for the driver, renders a two-second application-owned revision to 60 real frames, verifies the driver artifact manifest and retains first/middle/last review frames plus machine-readable evidence. A written workflow is not a PASS; the evidence belongs to the exact Motionwright SHA only after that run succeeds.

## Authority boundary

A valid local receipt means only that Motionwright recorded a Broker interaction. It does not imply render quality, Project Graph admission, Effect Conformance PASS, creative approval or freshness. Those claims require their canonical Semwright evidence paths.

## Job correlation projection

Motionwright does not persist a second scheduler or a competing runtime state machine. The Studio derives a bounded job ledger from its immutable production receipts and groups only the allowlisted Motion Canvas and MLT render job commands.

The projection keeps execution state separate from result applicability. CANCEL_REQUESTED is never rendered as CANCELLED until the canonical driver confirms cancellation. A definitive SUCCEEDED, FAILED or CANCELLED observation cannot be resurrected by a later incompatible active-state receipt. An OUTCOME_UNKNOWN observation may be reconciled by a later canonical observation. The original project generation/revision remains the applicability base, so a late successful result is displayed as STALE after the creative project advances.

Only safe correlation metadata is copied into dispatch/error receipts for job commands: the job reference when one already exists. Render arguments, private logs, session contents and arbitrary filesystem paths are not added to the job projection. Progress and artifact indicators appear only when the canonical driver actually returned them.

The same derived ledger is available to the desktop UI and through the Semwright Native SDK production-jobs observation scope. That scope exposes Motionwright-owned receipt history; it does not create runtime authority.
