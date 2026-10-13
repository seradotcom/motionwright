# Motionwright v0.1 — safe native AV failure diagnostics

**Release gate status: still blocked.** This replaces raw Python subprocess exception dumps in the CI-only Motionwright native AV runner with an allowlisted diagnostic. It does not fix the Semwright Broker/Driver Host lifecycle, restart native production, verify missing media, or authorize the v0.1 release.

## Why this exists

A real 2026-10-12 native AV master E2E [run](https://github.com/seradotcom/motionwright/actions/runs/38301150251) reported an acknowledged Motion Canvas render, followed by a read-only status Timeout and an Unavailable response that stated the provider generation was inactive. The already-started native render had **unknown** terminal status. The previous generic Python exception printed up to 8,192 bytes of raw CLI stderr and argv into GitHub job logs, including opaque job references and runner-private paths. Such logs must not be treated as publishable diagnostic evidence.

## Execution behavior

- tooling/native_av_master_e2e.py still runs the same exact-source canonical Semwright commands. On a nonzero exit, process timeout, OS spawn error or malformed successful JSON it raises NativeCommandFailure, whose message contains **only** constant, closed diagnostics, never command arguments, raw bytes or session material.
- tooling/native_av_failure_guard.py parses at most 256 KiB of a typed error response in memory. It emits only enumerated status and error codes, bounded counts and booleans: observed read-only Timeout/Unavailable, whether the provider generation was reported inactive, and whether a previously acknowledged native render start appears in the failure receipt window.
- **No raw receipts, messages, connection paths, media names, CLI stderr, stdout, executable paths, private job IDs or session tickets** are serialized in the diagnostic. Unknown or oversized responses fail closed and remain unverified.
- A failed native render exits the CI job nonzero. The producer saves native-render-failure-diagnostic.json tied to the exact Motionwright and Semwright source hashes. UNKNOWN is preserved for an unconfirmed started render; no automatic mutation or second render.start occurs.
- Normal successful commands retain the same parsed JSON and downstream frame/audio integrity requirements. Never substitute this diagnostic for terminal render.result, verified PNGs, audio samples or a mastered MP4.

## Tests and limitations

Nine standalone synthetic Python tests cover an adversarial receipt containing private-looking paths and tokens, status Timeout then lost generation, no secret leakage into exceptions/JSON, oversized/malformed responses, failed process deadline, unrecognized error code, strict no-auto-retry and unchanged successful-command behavior. The dedicated fast GitHub Actions workflow does not require an active native provider; the existing full native AV job also runs these tests before rendering.

A green redaction test is **not** a reproduction or correction of [provider issue #49](https://github.com/seradotcom/motionwright/issues/49). The actual reason for Driver Host generation deactivation, safe owner recovery, [foreground MLT cleanup #91](https://github.com/seradotcom/motionwright/issues/91), and independent owner-installed v0.1 acceptance remain unresolved.
