# Read-only native Film semantic preflight

Motionwright can check whether an exact saved project revision and selected delivery profile map to the **canonical pinned Semwright Film authoring contract** *before* dispatching a render job. This is not a second scheduler, Broker, permission engine or approximate renderer.

## Trust boundary

The Tauri `motion_canvas_preflight` command accepts project ID, expected generation/revision, selected deliverable ID and the same typed `FilmBuildOptions` used by native Motion Canvas render production. It loads the application-owned versioned project, verifies the exact source stamp, then invokes `build_motion_canvas_segments` from Motionwright's existing Semwright-native Film adapter inside a bounded blocking task. After the projection, it rereads the project revision to reject concurrent changes.

No effect grant is required because this is a nonmutating read of application-owned semantics. The command does not contact the canonical Broker or Driver Host, allocate a native render job, generate frames, download a renderer, modify locks or commit a creative change.

The typed response has project identity/generation/revision, saved profile identity, verdict `projection_ready` or `unsupported`, and a summary of segment IDs and frame counts **only when the semantic projection succeeded**. Empty cut, nonrepresentable keyframes/camera zoom or rotation, unsafe camera pan framing, missing palettes, malformed scene authoring or frame-rate drift return an explicit semantic rejection without fallback approximation. The native projection admits only [exact linear paired position keys](native-linear-position-motion.md) under strict frame/time/camera/safe-area bounds. Exact static camera pan (zoom 1, rotation 0) is supported only when the projected subjects pass the original output safe-area rules; see [static Film camera pan](native-film-static-pan.md).

## Operator flow

Deliver retains the production button. The separate **Check Film projection** button is enabled only after the editor already has saved profile and explicit narrative role/archetype inputs. The result is keyed to project generation/revision, profile and exact Film options: changing the scene intents, selection or creative revision makes the previous preflight stale and hides it. A verified **unsupported** result blocks render dispatch for that exact unchanged input, but does not permanently lock the editor; revise content then recheck.

A **projection_ready** result does *not* assert that Semwright's renderer, Driver Host, fonts, sandbox, media timings, output files, Effect Conformance or AV mastering will pass. Only actual native execution and verified artifact readback can supply that evidence.

Browser demo cannot claim canonical Film projection readiness; it disables the action rather than simulating the backend. Synthetic browser regressions test the UI and transport only; native Film unit suites exercise real semantic projection at the pinned source SHA.

## CI evidence

- Existing pinned Semwright canonical Film tests exercise supported/unsupported projection and deterministic frame plans.
- Tauri Linux and Rust CI compile the new read-only command and exact source boundary.
- Chromium synthetic Tauri tests exercise supported/unsupported outcomes, typed arguments, nonmutation, no unexpected native render dispatch and invalidation on changed creative inputs.

Independent end-user creative and production acceptance remains separately required.
