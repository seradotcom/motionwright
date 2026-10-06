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

The provider exposes project-level `summary`, `timeline`, `brief`, `narrative`, `audio`, `visual-language`, `canvas`, `alternatives`, `history` and `locks` scopes.

Canvas observations return semantic object/camera state; they do not claim that a native renderer produced matching pixels. History returns only application-owned committed journal rows. It is not a reconstruction of private model reasoning.

## Typed operations

The provider intentionally exposes composable commands instead of an unrestricted patch endpoint:

- project: rename and set brief;
- narrative: set premise;
- scene: add, move, set objective, renderer, review status and rational duration with explicit ripple;
- canvas: add/remove nodes, transform, set/clear text, replace validated style, reparent/z-order, replace semantic relations, set property locks and set camera;
- timeline: add marker;
- assets: register an already-ingested SHA-256-addressed blob and remove an unreferenced project asset; registration fails if the app-owned blob store cannot verify the bytes;
- creative system: set visual language, add a bounded proposal set and select one proposal;
- authority hints owned by the app: set and remove explicit project/resource locks.

Together with `driver.motionwright.observe`, this is currently 27 Native SDK capabilities.

Proposal selection records intent only. It does not execute the proposal's edits or bypass the normal project locks, revision CAS, Broker/Policy or Driver Host boundaries. Scene-duration ripple changes presentation timing and later scene starts; it does not silently retime measured audio.

## Shared UI and agent state

The React/Tauri editor calls the same application service and `Change` domain used by the Native SDK adapter. Browser-only development fixtures mirror those change contracts for UI tests, but are not runtime evidence.

Operation discovery never authorizes an invocation. Broker/Policy/Driver Host remain responsible for capability, consent, session and runtime authority.
## Canonical Graph and Effects adapters

Motionwright now enables the Native SDK `graph` and `effects` features on the exact Semwright pin. The native crate can serialize the exact application-owned project revision into an **untrusted** `RevisionCandidate` plus durable native locator for the canonical Project Graph, and can prepare the canonical protected Effects specification from bounded JSON input.

These helpers intentionally stop before authority. A Motionwright process cannot admit its own Graph evidence, decide CURRENT / STALE / UNKNOWN, or mint an Effect Conformance verdict. Graph admission still requires the trusted Graph owner and authenticated Driver Host session; Effects readback still runs against immutable admitted artifacts under the Semwright-owned protected path.

The dedicated `Canonical Graph and Effects` workflow re-runs the upstream adapter/conformance tests at the exact `SOURCE_LOCK.json` SHA and then exercises Motionwright's consumer wrappers.

The provider also exposes production-jobs, a read-only projection derived from Motionwright production receipts. The job scope is application history only: Semwright remains the scheduler/runtime authority, and CURRENT/STALE applicability is evaluated against the open Motionwright revision.

The desktop Workflow workspace uses the same owner-provisioned `ProductionConnection` boundary for canonical Semwright workflow distillation. Reads plus the explicitly supported record/compile/plan/accept/verify/replay/promote commands are hard-allowlisted and must carry built-in `semwright-core` provenance. Replay is treated as a mutation for uncertain-outcome handling even though Semwright supports planning/dry-run semantics. Destructive or unrelated workflow commands are not exposed by Motionwright; see `workflows.md`.
