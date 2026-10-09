# Semwright Native SDK integration

Motionwright pins the Semwright revision recorded in `SOURCE_LOCK.json` and consumes the public `semwright-native-sdk` crate from that immutable Git revision.

The integration follows the Native SDK public contracts:

- `Application::new("motionwright", ...)` describes the cooperation surface.
- `ObservationProvider` enumerates bounded application-owned projections.
- `OperationHandler` executes only registered typed operations.
- the application-owned SQLite transaction checks `CallContext::expected()` at commit time;
- opaque revisions are strings at the SDK boundary, never lossy JavaScript counters;
- durable request IDs make retry behavior explicit;
- uncertain completion remains uncertain;
- Native SDK Graph and Effects features adapt into canonical authorities rather than creating local verdict engines.

## Observation scopes

The provider exposes project-level `summary`, `timeline`, `brief`, `narrative`, `audio`, `deliverables`, `visual-language`, `canvas`, `alternatives`, `history`, `history-recent`, `locks`, `branches`, `reviews`, `merges`, `production-jobs` and `production-receipts` scopes.

Canvas observations return semantic object/camera state; they do not claim that a native renderer produced matching pixels. History returns only application-owned committed journal rows. It is not a reconstruction of private model reasoning. **All versioned project-array scopes now expose truthful continuation cursors** rather than truncating a long list and claiming it is complete; see [Native SDK scope pagination](native-scope-pagination.md).

The SDK exposes two **revision-bound, read-only** history scopes. Existing `history` preserves its original oldest-first order and now returns a `PageCursor` when more chronological entries remain. `history-recent` starts with the newest recorded commit and advances backwards by revision, matching the Changes workspace's recent-first journal. Both scopes use strictly bounded keyset reads (no OFFSET), a cursor tied to resource/generation/revision and scope, and truthful `complete`/`next` fields. A cursor from another revision, a malformed token or an attempted order swap is rejected. The native observer checks the project version again after reading so it cannot attribute a concurrent write to an earlier observation. See [recent-first pagination](history-pagination.md).

## Typed operations

The provider intentionally exposes composable commands instead of an unrestricted patch endpoint:

- project: rename and set brief;
- narrative: set premise;
- scene: add, move, set objective, renderer, review status and rational duration with explicit ripple;
- canvas: add/remove nodes, transform, set/remove typed rational-time keyframes, set/clear text, replace validated style, reparent/z-order, replace semantic relations, set property locks and set camera;
- timeline: add marker;
- assets: register an already-ingested SHA-256-addressed blob and remove an unreferenced project asset; registration fails if the app-owned blob store cannot verify the bytes;
- creative system: set visual language, add a bounded proposal set and select one proposal;
- authority hints owned by the app: set and remove explicit project/resource locks.

Together with `driver.motionwright.observe`, this is currently 46 Native SDK capabilities, including `canvas.position-keyframe.set` for atomic paired X/Y motion keys and `canvas.motion.linear-position.set` for one frame-bound four-key native motion authoring transaction.

Proposal selection records intent only. It does not execute the proposal's edits or bypass the normal project locks, revision CAS, Broker/Policy or Driver Host boundaries. Scene-duration ripple changes presentation timing and later scene starts; it does not silently retime measured audio.

## Shared UI and agent state

The React/Tauri editor calls the same application service and `Change` domain used by the Native SDK adapter. Browser-only development fixtures mirror those change contracts for UI tests, but are not runtime evidence.

The dedicated Native SDK workflow also exercises a **real Host shared-state lane** on a disposable Linux runner. It launches the exact pinned Semwright daemon/Broker/Driver Host, the Motionwright stdio Native SDK provider and a separate app-state actor using the same `StudioService` path as Tauri. The lane proves that app-owned writes are visible through Host, SDK writes are visible when the application reopens the same SQLite store, and an app write invalidates an older Native SDK reference with `StaleReference`. The actor is test evidence for the shared application-service boundary; it is not a substitute for independent end-user UI acceptance, which remains separately gated.

Driver Host supplies the provider with an owner-granted `motionwright-data` workspace mount. The provider never accepts a database path from an operation payload. Explicit `MOTIONWRIGHT_DB` or positional database paths remain development/operator entry points only when the provider is launched directly outside Host.

Operation discovery never authorizes an invocation. Broker/Policy/Driver Host remain responsible for capability, consent, session and runtime authority.

## Production runtime compatibility

Motionwright does not bundle, download or auto-install a Semwright runtime. Desktop production uses the owner-provisioned `MOTIONWRIGHT_SEMWRIGHT_CONNECTION` file and the same `ProductionConnection` validation used by canonical rendering and workflow calls.

The Integrations workspace now exposes a bounded compatibility probe. It first revalidates the connection file and the configured Semwright executable SHA-256, then launches that exact executable with `--version` only. The probe has bounded stdout/stderr, a short timeout, no project mutation and no effect grant because it is read-only. The observed CLI version must match the version recorded in `SOURCE_LOCK.json`.

The UI keeps three facts separate:

- the Semwright source revision pinned by Motionwright;
- the SHA-256 of the owner-provisioned executable recorded by the connection;
- the CLI version observed from that digest-pinned executable.

An observed `--version` string is **not** treated as an attestation of the executable's source commit. A valid source pin plus a matching CLI version therefore reports runtime compatibility, not supply-chain provenance beyond the evidence actually checked. Browser demo mode never fabricates a live runtime result.

## Canonical Graph and Effects adapters

Motionwright now enables the Native SDK `graph` and `effects` features on the exact Semwright pin. The native crate can serialize the exact application-owned project revision into an **untrusted** `RevisionCandidate` plus durable native locator for the canonical Project Graph, and can prepare the canonical protected Effects specification from bounded JSON input.

These helpers intentionally stop before authority. A Motionwright process cannot admit its own Graph evidence, decide CURRENT / STALE / UNKNOWN, or mint an Effect Conformance verdict. Graph admission still requires the trusted Graph owner and authenticated Driver Host session; Effects readback still runs against immutable admitted artifacts under the Semwright-owned protected path.

The dedicated `Canonical Graph and Effects` workflow re-runs the upstream adapter/conformance tests at the exact `SOURCE_LOCK.json` SHA and then exercises Motionwright's consumer wrappers.

The provider also exposes `production-jobs`, a read-only projection derived from Motionwright production receipts. The job scope is application history only: Semwright remains the scheduler/runtime authority, and CURRENT/STALE applicability is evaluated against the open Motionwright revision. Since production receipts can change without a creative revision, both production scopes use a separate **receipt-stream watermark**. `production-receipts` returns newest-first keyset pages of **metadata only**, omitting raw provider payloads. `production-jobs` reconstructs a consistent local job snapshot across all receipts up to a documented 20,000-receipt safety budget and offers bounded continuation pages. An independent appended receipt invalidates either cursor; oversized histories error explicitly rather than claiming completeness. See [production receipt/job pagination](production-receipt-pagination.md) and [project-array scopes](native-scope-pagination.md).

The desktop Workflow workspace uses the same owner-provisioned `ProductionConnection` boundary for canonical Semwright workflow distillation. Reads plus the explicitly supported record/compile/plan/accept/verify/replay/promote commands are hard-allowlisted and must carry built-in `semwright-core` provenance. Replay is treated as a mutation for uncertain-outcome handling even though Semwright supports planning/dry-run semantics. Destructive or unrelated workflow commands are not exposed by Motionwright; see `workflows.md`.
