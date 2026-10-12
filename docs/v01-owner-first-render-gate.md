# Motionwright v0.1 — first owner-native render dispatch gate

**Scope:** V0.1. This is a shipping UX and safety improvement for **Deliver**, not a v0.5 renderer integration, a new scheduler, a parallel backend, or release acceptance.

## First render from a fresh installed application

1. Work on a saved project with a saved delivery profile. Include at least one explicit Motion Canvas scene and record its narrative role and archetype. Define explicit background/surface and text/ink tokens.
2. In Integrations, select the **owner-provisioned** Semwright connection through the trusted OS picker. Nothing in this flow installs or grants a renderer.
3. In Deliver, select the intended profile and click **Check Film projection**. This validates the exact project generation, revision, deliverable and authoring input set through the existing trusted desktop Film projection path. It does not execute a provider job.
4. Deliver asynchronously performs the existing read-only SHA-256/CLI-version compatibility probe for that project revision; the trusted Tauri handler now checks the optional expected generation/revision before and after the asynchronous probe (current Studio always sends both); **Recheck Semwright CLI** explicitly retries when the owner changes the local connection. A probe result from an earlier revision cannot enable rendering.
5. **Render native segments** is enabled only after both checks pass. On click it checks the CLI again, **before** requesting a one-time effect grant, because the owner can revoke or replace runtime files after the earlier observation.
6. The existing Tauri effect-grant scope, native Semwright Broker/Driver Host provenance verification, provider status receipt and Native SDK pin remain the execution authority. A successful local version check is **not** a Broker handshake. A successful Film plan is **not** a pixel/media proof. A failed or unknown native dispatch never becomes a successful render through this UI gate.

## Fail-closed cases

Browser demo, unsaved profile, incomplete intent, missing palette, absent/rejected/stale Film preflight, missing/revoked/wrong-version runtime, invalid executable or connection identity, or a runtime observation from a previous generation/revision all disable native dispatch. Any authoring change invalidates the preflight; even after the button becomes available, the runtime is rechecked synchronously in the click flow **before** delegating to the existing authorized native command. The UI never reads a connection path/session token, grants effects or synthesizes provider readiness.

No text here authenticates an externally provided binary against the Semwright Git pin: digest comparison is local and owner-provisioned, not a signed provenance attestation.

## Evidence

- Pure Studio contract: `apps/studio/src/firstNativeProduction.ts`, with adversarial Vitest tests for scope, version, digest, Film frame accounting and untrusted observations.
- Browser acceptance: `apps/studio/tests/v01-owner-first-render.spec.ts`. The fixture simulates an owner revoking runtime compatibility **after** the UI became eligible. It verifies no effect token or render dispatch is requested. Existing synthetic native editor tests now first perform an explicit Film and CLI compatibility preflight.
- GitHub Actions CI runs Studio unit tests, TypeScript production build, Playwright browser acceptance and the desktop Rust unit contracts for strict revision-scoped probing; no heavyweight compilation/rendering on the owner workstation is necessary.

**Current release state:** v0.1 remains blocked by 208 requirement verification, 60 private acceptance cases, real installed first owner-native render+readback, provider reliability/cleanup, upstream advisories and physical-device signing/distribution review (issue #126). This code cannot mark any of them passed without independent evidence.
