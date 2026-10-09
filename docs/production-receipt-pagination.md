# Native SDK production receipts and jobs: exact receipt-stream cursors

Motionwright's creative project revision and its local production receipt stream advance **independently**. A native render may produce ten status receipts without committing a creative edit. A cursor bound to project revision alone is therefore insufficient for production history. Earlier app-owned Native SDK observations correctly disclosed `production-jobs` as **partial/unknown** because they projected only the newest 256 receipt rows and provided no continuation.

This feature supplies the missing local-receipt snapshot contract without becoming a second scheduler or querying the canonical Semwright Driver Host. Rendering, cancellation, status authority and backend recovery remain upstream-owned.

## Append-only local receipt watermark

The existing SQLite `production_receipts` table has a project-scoped indexed `receipt_id` (UUIDv7) and `request_sha256` records. A new **read-only stream watermark** consists of:

- the highest persisted UUIDv7 receipt ID for the project;
- the exact **count** of records in that project stream at that observation.

The count matters: an append within the same millisecond might sort before the largest UUIDv7 ID. A greatest-ID-only watermark would miss that change. The pair is checked before and after a page read. Any append detected between pages invalidates the old cursor with `StaleReference`, **even if the creative project revision is unchanged**. Concurrent creative edits are rejected independently using the pinned Native SDK `ResourceVersion`.

A receipt cursor has the form `receipts:v1:<highest-uuid>:<count>:<before-uuid>`, bound to the SDK resource, generation/revision, and `production-receipts` scope. It pages newest-first through **bounded SQLite keyset reads** (`receipt_id < before`, capped at 256 records by storage), with a one-item lookahead. `complete: true` means that every persisted receipt through the verified watermark has been enumerated, not that Semwright has no future work to do.

## Read-only receipt metadata: no raw provider payload

The new `production-receipts` observation scope returns bounded, application-owned metadata only: receipt ID, generation, revision, request ID, request SHA-256, command name, stage and observation timestamp. The raw `payload_json` can include untrusted driver responses, source paths or private information and **is never emitted in this Native SDK observation**. The original internal owner service still holds the full receipt for permitted reconciliation and project operations; no general filesystem or driver-reading capability is introduced.

## Full local production-job pages

The `production-jobs` scope now builds a version-stable derived job snapshot from **the entire persisted receipt stream as of the same verified watermark**, not only the newest 256 receipts. It reuses Motionwright's existing job projection semantics: observed job state, cancellation request vs confirmed cancellation, definitive terminal state, provider generation, progress/result evidence and separate CURRENT/STALE creative applicability.

The job cursor is `jobs:v1:<highest-uuid>:<count>:<offset>`; offset is safe **only inside a frozen, checked receipt snapshot**. Jobs are ordered by last observation time then stable job reference, just as the preexisting UI projection. The native provider reports `complete: true` only on the actual final page, and `next` whenever more job rows remain.

For safety, this on-demand exhaustive job reconstruction refuses streams exceeding **20,000 receipts** rather than loading an unbounded ledger in memory or fabricating completeness. This is a deliberate and explicit `InvalidArgument` error at the service boundary, **not** a successful partial response. Larger enterprise histories require independently indexed persisted job summaries and an explicit migration before the guardrail can be lifted safely.

The existing Studio `Production Jobs` panel continues using its independent bounded most-recent 256-receipt/64-job view for interactive responsiveness; it is not silently converted into a full-history scan. Semwright remains the job runtime authority.

## Tests and evidence

- Storage tests create **311 real SQLite receipts**, exhaust descending keyset pages without overlap, check project isolation, the existing latest-256 compatibility behavior, and independent stream watermark changes.
- Native SDK tests create **340 real app-owned production receipts for 85 jobs**. They prove complete, ordered, duplicate-free enumeration of every receipt and job in pages smaller than both lists, verify that raw provider/private source paths never cross the Native SDK response, and ensure the reconstructed job projection retains all four observations per job.
- A new status receipt is appended **without a creative revision change**. Both previously issued receipt and job cursors then fail with `StaleReference`; malformed cursor grammars are rejected. Truly empty streams are provably complete and yield no continuation.
- All tests run in the exact-SHA GitHub CI lanes. No output here certifies Semwright driver's runtime current status, media quality, Graph admission or independent product acceptance.

This operation does not alter Semwright's immutable pinned Native SDK SHA or broaden Driver Host authority.
