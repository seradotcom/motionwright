# Motionwright v0.1 — MLT native project lifetime inventory

**Status: partial, no native cleanup executed.** Issue #91 requires trusted foreground Broker approval before a destructive MLT project close. The pinned application does not yet have a separately verified authorization and completion contract for that action.

## What this implements

The installed Jobs workspace provides an explicit **MLT lifetime** button. It reads only Motionwright's locally persisted source-bound production receipts; it never queries Semwright, opens a runtime connection, issues grants, starts another render or changes an MLT project.

The service reconstructs a bounded complete 20,000-receipt maximum snapshot under a stable append-only receipt watermark and checks the exact project generation and revision both before and after reading it. Each native project creation counts only after a completed exact MLT project.create result, admitted driver provenance and provider generation, a bounded opaque native reference and valid 64-character semantic revision digest. Duplicate stages of the same logical request are counted once. Historical and current creative revisions are distinguished.

A dispatching, failed or malformed creation counts as **unconfirmed**, not an existing project. A project.close command in the journal is counted as an **observed attempt**, never proof of owner consent or actual resource deletion. Even a completed provider close response is insufficient to change that rule. Opaque native refs, paths, connection tokens and receipt contents never cross into the browser result.

Every inventory reports, without exceptions: native_provider_liveness_verified=false, foreground_cleanup_consent_obtained=false, verified_native_project_cleanup=false, automatic_destructive_cleanup_allowed=false. Provider lifetime may end independently of the local journal. The inventory is not a live MLT allocation count.

## Independent real provider conformance

The existing pinned native MLT 33-frame CI now additionally compares this read-only inventory against the **actual completed MLT project.create receipts persisted in SQLite** (not fixture objects) in PCM, video-only and final MP4 runs, with independent JSON SHA-256 checks. See [real MLT lifetime evidence](v01-real-mlt-lifetime-evidence.md). Passing proves only source-bound create accounting, not provider liveness or cleanup consent.

## Next authorization gate

The application needs a true foreground consent implementation backed by the original authenticated Semwright Broker and an unforgeable one-time destructive effect grant, with specific native project reference, generation and revision, and verified provider completion semantics. Do not substitute a JavaScript confirmation, a CLI-only probe or a Motionwright local receipt for actual Broker authorization. Only after this independent gate may issue #91 be considered for closure.

## Validation

Unit regressions cover completed source authority, duplicate request stages, unknown starts, foreign/invalid provenance, provider generation isolation, historical revisions, partial receipt windows and untrusted close responses. Studio/browser acceptance verifies the action is read-only and refuses forged scope evidence. GitHub Actions runs Rust and browser tests on the pinned SDK; this is not an independent installed-owner cleanup acceptance.

Issue #126 remains BLOCKED_PENDING_EXTERNAL_EVIDENCE; all 60 private acceptance cases are NOT_RUN.
