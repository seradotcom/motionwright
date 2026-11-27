# Motionwright v0.1 — MLT native project lifetime inventory

The MLT lifetime inventory reads the local journal. A successful MLT timeline job in the installed Jobs workspace also offers a separate foreground closure request.

## What this implements

The installed Jobs workspace provides an explicit **MLT lifetime** button. It reads only Motionwright's locally persisted source-bound production receipts; it never queries Semwright, opens a runtime connection, issues grants, starts another render or changes an MLT project.

The service reconstructs a bounded complete 20,000-receipt maximum snapshot under a stable append-only receipt watermark and checks the exact project generation and revision both before and after reading it. Each native project creation counts only after a completed exact MLT project.create result, admitted driver provenance and provider generation, a bounded opaque native reference and valid 64-character semantic revision digest. Duplicate stage observations of the same logical request and same native reference are counted once. An already completed, provenance-admitted project.create cannot disappear from the read-only inventory merely because a later dispatching, failed or outcome-unknown stage was appended for that request. If two different native project references were genuinely returned as completed results for the same request, both distinct historical creations remain visible rather than hiding a potential extra provider allocation. Historical and current creative revisions are distinguished. This does not assert that either resource is still live.

A dispatching, failed or malformed creation counts as **unconfirmed**, not an existing project. The source-projection deduplication key includes the command, request ID, creative project generation **and revision**, not the request ID alone. Production storage permits the same command/request digest/ID to recur in a later creative revision; a completed create in one revision must not erase an `UNKNOWN` create in the other, and conversely. In the rare event of a forged or malformed cross-command collision, `project.close` also cannot overwrite a previously confirmed create in this read-only projection; normal storage already rejects reuse across different commands. A project.close command in the journal is counted as an **observed attempt**, never proof of owner consent or actual resource deletion. Even a completed provider close response is insufficient to change that rule. Opaque native refs, paths, connection tokens and receipt contents never cross into the browser result.

Every inventory reports, without exceptions: native_provider_liveness_verified=false, foreground_cleanup_consent_obtained=false, verified_native_project_cleanup=false, automatic_destructive_cleanup_allowed=false. Provider lifetime may end independently of the local journal. The inventory is not a live MLT allocation count.

## Independent real provider conformance

The existing pinned native MLT 33-frame CI now additionally compares this read-only inventory against the **actual completed MLT project.create receipts persisted in SQLite** (not fixture objects) in PCM, video-only and final MP4 runs, with independent JSON SHA-256 checks. See [real MLT lifetime evidence](v01-real-mlt-lifetime-evidence.md). Passing proves only source-bound create accounting, not provider liveness or cleanup consent.

## Close an original MLT project

Select **Request MLT project closure** on the successful job in Jobs. Use the original production connection with its MLT provider and output workspace still active. The owner’s Broker policy must allow the bound transport command `providers.execute_bound` and read access to the existing output mount. Run the Broker with its foreground approval console and answer its original challenge there while the provider binding is still active. The binding expires 60 seconds after it is created.

Motionwright binds the saved job and exact output to the original native project generation and semantic revision. The app records a single-use dispatch in SQLite before requesting closure. A confirmed closure releases the in-memory native project. The rendered media stays available. A declined request or a request with an uncertain outcome remains fenced across reopening. If the Broker definitively reports that confirmation was unavailable, a new explicit request can use the same original project after the console is available.

Real foreground closure verification remains open in issue #91.

## Validation

Unit regressions cover completed source authority, duplicate request stages, unknown starts, foreign/invalid provenance, provider generation isolation, historical revisions, **request/digest reuse across creative revisions with separate UNKNOWN accounting**, partial receipt windows and untrusted close responses. A real SQLite/StudioService regression creates a proven MLT project, advances the project's creative revision and appends an UNKNOWN project.create with the same request ID/digest; the snapshot must retain both the older proven resource and newer unconfirmed request across a database reopen. Studio/browser acceptance verifies the action is read-only and refuses forged scope evidence. GitHub Actions runs Rust and browser tests on the pinned SDK; this is not an independent installed-owner cleanup acceptance.

Issue #126 remains BLOCKED_PENDING_EXTERNAL_EVIDENCE; all 60 private acceptance cases are NOT_RUN.
