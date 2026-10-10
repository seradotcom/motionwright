# Cross-app selective recovery: native Blender → Motion Canvas

**Status: experimental / CI evidence required.** This feature is a bounded two-provider recovery example, not a universal dependency-graph scheduler, and not a declaration that full Motionwright production is complete.

## What the code guarantees

- Starts from Motionwright's application-owned, versioned SQLite project and its semantic scene projections.
- A real Blender stage executes through Semwright Broker/Driver Host and exports a bounded GLB. Its receipt stores a digest-bound proof; an unchanged scene projection and generation may reuse that proof even after another creative scene changes.
- A Motion Canvas stage renders the *separate* authored 2D segment using Semwright. Only a verified terminal native render may be marked completed. The artifact-manifest hash plus each PNG frame's size and SHA-256 are checked again before reuse.
- Separate application processes reopen the same SQLite receipts. A fresh process does **not** redispatch any Blender command when it can verify the completed Blender artifact.
- Each new stage is claimed transactionally using SQLite `BEGIN IMMEDIATE`; a dispatching/unknown result survives crashes and **blocks** blind retry. A new request cannot evade an unsettled same-provider mutation merely by renaming the flow or changing its input fingerprint. Semwright retains provider authority, approval, cancellation and native execution.
- Motion Canvas is automatically retried only when the *native renderer* explicitly returns a known terminal `failed` state in the supported bounded category, not when a status response times out or the provider generation disappears. Blender's partially mutated workspace must be reconciled before any unsafe rerun.

## Real native demonstration

The exact-source GitHub Actions lane [`native-cross-app-recovery.yml`](../../.github/workflows/native-cross-app-recovery.yml) builds the real **Blender 4.5.14 LTS** and **Motion Canvas 3.17.2** providers with the pinned Semwright Native SDK revision from `SOURCE_LOCK.json`. It uses one ephemeral Semwright daemon, Broker/Driver Host and shared owner-mounted output root, and three *separate* Motionwright processes:

1. `seed`: create a persisted creative project containing one Blender scene and one Motion Canvas scene.
2. `stop`: dispatch and verify native Blender GLB via the Broker; write a completed immutable receipt and intentionally terminate the application **before Motion Canvas dispatch**.
3. `resume`: reopen SQLite in a new process; prove the Blender GLB hash is unchanged and **zero** Blender driver commands were executed again; render 60 real Motion Canvas frames.
4. `repeat`: reopen SQLite in another process; verify the entire Motion Canvas manifest and all 60 PNG hashes again and reuse both valid stages without redispatch.

The acceptance script `tooling/native_cross_app_recovery_e2e.py` publishes `stop.json`, `resume.json`, `repeat.json`, `result.json`, the original reused `reused-blender.glb`, a native Motion Canvas manifest, review PNG frames, and exact source revisions in a GitHub Actions artifact. A workflow file is **not evidence of PASS** until its job succeeds at that exact commit. CLI phases live in `crates/native/examples/native-cross-app-recovery-e2e.rs`.

**Important limitation:** The controlled `stop` deliberately interrupts *between* native applications; it **does not simulate a failure within the Motion Canvas renderer**. The Rust recovery tests also cover a confirmed Motion Canvas failure followed by attempt 2 after SQLite reopen, but that specific failure test is scripted rather than a real native-render-failure E2E. Do not advertise the latter as validated until an independent native failure-injection case passes.

**Other explicit limitations:** the GLB remains its own editable Blender contribution, *not* composited into Motion Canvas frames; no finished audio/video master or real-time UI animation is claimed. This does not yet prove generic dependency invalidation over arbitrary creative apps, cross-machine state migration, or repairing a lost provider generation. A real MC terminal failure, full cross-app native readback, and human usability acceptance remain independent gates.

## Local correctness gates

```bash
cargo fmt --all --check
cargo test --locked -p motionwright-native recovery -- --nocapture
cargo build --locked -p motionwright-native --example native-cross-app-recovery-e2e
python3 -m py_compile tooling/native_cross_app_recovery_e2e.py
```

No `curl | bash` shortcuts or fake driver responses are used in the **native acceptance lane**. The separate fast Rust stage tests use deterministic fixtures, do not claim to be a real third-party renderer invocation, and enforce uncertain-outcome refusal.

## Safety and provenance

- Full agent sessions, native command arguments and local output paths are not placed in the public aggregate `result.json`; process details remain in private ephemeral evidence as configured by CI.
- SHA-256 alone does not prove permissions, semantic conformance, creative quality, or execution authority. The trusted Broker, native driver identity, and manifest-level validation remain mandatory.
- A missing, corrupted or replaced artifact **fails closed** rather than silently regenerating it.
- A known failed Blender mutation is not automatically replayed; manual diagnosis and recovery through Semwright's canonical observation and policy boundary are required.
- Late results from an old project generation cannot be treated as current results.

## Acceptance ladder

| Gate | Required proof |
|---|---|
| Fast semantic recovery | All deterministic Rust tests PASS |
| Native Blender export | Exact pinned Blender/Driver Host, validated GLB |
| Cross-app restart without duplicate work | Three separate processes, zero Blender redispatch, complete 60-frame Motion Canvas readback |
| Confirmed native renderer failure then resume | A real terminal MC failed state followed by a verified successful native retry — **not yet proven** |
| Finished editable multi-renderer video | Native composition/assembly plus creative and human review — **not covered** |
