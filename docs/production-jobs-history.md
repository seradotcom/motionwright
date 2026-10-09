# Production Jobs: browse older local receipts

The normal Motionwright Production Jobs panel remains a **lightweight recent-window view**: it reads at most 256 app-owned receipts, derives up to 64 recent jobs, and periodically rereads only while visible jobs are running. This behavior avoids making every editor refresh an expensive reconstruction of historical production state.

The desktop now also offers **Full history**, an explicitly initiated read-only view of all locally persisted job projections within the same source-bound history contract used by the Semwright Native SDK. It does not dispatch rendering, poll the canonical Driver Host, change job state, request cancellation or advance the creative project revision.

## Historical snapshot

The Tauri `production_jobs_history` command requires the exact project ID/generation/revision, an optional previous server-provided receipt cursor, and a 1–64 job page size (Studio uses 16). The desktop fetches the current receipt high-water mark (**latest UUIDv7 ID plus exact receipt count**) through the existing Motionwright service and uses its full locally derived job projection. It returns a page of job summaries, total jobs and an optional next cursor. It never returns raw provider payloads, arbitrary source paths or secret-bearing receipt data.

A new native job observation can append a local receipt without committing a creative edit. If that happens between pages, the old receipt cursor becomes **stale**, even when the project's revision stays the same. The UI preserves rows from the earlier snapshot, states that their provenance needs rechecking, stops loading further pages and offers **Restart history**. It never mixes job rows from two independent stream watermarks or silently advances cursor offsets across a changed receipt stream.

The same native bounded reconstruction policy refuses histories above **20,000 receipts** rather than allocating unlimited memory or falsely claiming complete results. A read-only history cannot certify a live driver's current state merely by displaying the last locally recorded observation.

## User experience

The Jobs header includes **Full history** and **Recent jobs** options. Full history is enabled only in the desktop runtime, never in a browser-demo fixture; it loads the first page only on user request, with a count and a Load older jobs control. Restart history requests a new receipt watermark and returns to page one. The application maintains the recent polling loop separately; closing Full history returns to the latest-window view without mutating the project or a live job.

The historical table reuses existing Job/Provider/Execution/Base/Applicability/Last observation columns. Cancellation remains **requested** until the native driver confirmed the cancellation, and a successfully executed old render can still be **STALE** for the current creative revision.

## Evidence boundary

- Rust storage and Native SDK tests from [production receipt pagination](production-receipt-pagination.md) supply the authoritative 311-receipt keyset and 340-receipt/85-job state reconstruction evidence.
- Desktop compilation tests cover the on-demand read-only Tauri command with the same source- and receipt-watermark checks, and a blocking worker prevents unbounded synchronous scans on the UI thread.
- Synthetic Chromium tests verify 42 historical jobs over three 16-job pages, scoped cursor/request arguments, no driver mutation, deduped rows, explicit stale error after a new receipt, restart semantics and no browser-demo fake history.

These are code/runtime contract tests, **not** independently reviewed end-user product acceptance. No native rendering is certified by synthetic Tauri fixtures.
