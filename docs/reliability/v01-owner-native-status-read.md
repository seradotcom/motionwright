# Motionwright v0.1 — owner-initiated original-job status observation

**Scope:** A bounded, read-only recovery aid for an existing acknowledged Motion Canvas render. This is **not** a provider lifecycle fix (#49), a new render, terminal-result verification, native MP4 proof, user acceptance or release authorization.

## Workflow

1. Open **Jobs** in an installed Motionwright desktop build with the same original project generation and revision.
2. On a current Motion Canvas job marked **OUTCOME UNKNOWN** or **CANCEL REQUESTED**, explicitly choose **Check original status**. Browser mode and stale/historical/other-provider rows cannot request the operation.
3. The trusted Tauri backend snapshots the project's source-bound receipt watermark and its complete local job projection (bounded to 20,000 receipts). It requires the exact **completed** native render.start receipt with the same opaque job ID, root request ID, original project, generation, revision and Driver Host provenance. A user-typed or forged job ID is never authority.
4. The desktop backend reopens the trusted saved owner connection, checks its exact project resource and calls the pinned Semwright Native SDK CLI/Broker once with **driver.motion-canvas.render.status** and mutation=false. No render.start, render.cancel, render.result, project.close, provider provisioning, restart or effect grant is attempted.
5. The native adapter requires the same job ID and **same provider generation as its acknowledged original start**. If the provider is unavailable, times out, changes generation, has malformed identity or reports an unknown state, the result stays **UNKNOWN**. Driver messages and private session paths are not disclosed.
6. Project generation/revision and the independent receipt watermark must still match after the call. Otherwise the response is rejected as stale.
7. Studio shows a scoped, temporary source status only. The durable historical job projection is not mutated by this check. Even when status reports succeeded, Studio says succeeded unverified: there is **no** result, composition verification, decoded frame, audio, hash or final MP4 validation in this operation. New production requires separate deliberate authorization.

## Security and reliability boundaries

- No arbitrary command or filesystem path is accepted from JavaScript. The command name, read-only bit and minimal job_ref argument are fixed in Rust, after exact local receipt authorization.
- No retry loop. At most one provider status request follows each explicit user gesture. On failure the owner may inspect and decide, but neither app nor task silently replays a mutation.
- The recent Jobs table can be an incomplete 256-receipt window. Authoritative gating reconstructs a bounded complete stream snapshot with a watermark, not just the visible row.
- This is **not** an interactive Broker consent substitute. It never calls the destructive MLT project.close from issue #91.
- The read-only observation is intentionally not persisted as a successful render: that would be wrong without native render.result, composition verification and media digest checks.
- If original provider generation is gone permanently, operator recovery retains UNKNOWN. Pinned Semwright Driver Host reliability must still be investigated independently.

## Verification

Pure Rust negative contracts cover acknowledged start correlation, foreign job IDs, foreign generations, unacknowledged starts, false provider status, provider generation changes, and never allowing verified-media or automatic-retry claims. Browser tests check explicit owner click only, exact IPC scope, no rendering/grants/cancels, and rejection of fabricated success. Existing CI separately tests the real pinned native renderer; those unrelated media tests are not owner-installed recovery acceptance.

An independent owner must still perform a real interrupted native job recovery on an installed build with its own Broker/Driver Host. Do not mark v0.1 issue #126, issue #49 or any of the 60 private acceptance cases complete based on these automated gates.
