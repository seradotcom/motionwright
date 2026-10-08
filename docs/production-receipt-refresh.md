## Local receipt rechecks

The Production Jobs workspace reads Motionwright's persisted, versioned production receipt projection through the existing `production_jobs` Tauri command. A manual **Refresh receipts** action rereads it without sending a native render/status/cancellation request, modifying the project, or minting a new runtime receipt.

While the Jobs workspace is mounted in the visible desktop application, it performs at most one read at a time and schedules the next read **12 seconds after** a successful read only if at least one projected job is `QUEUED`, `RUNNING` or `CANCEL REQUESTED`. It stops scheduling on terminal or `OUTCOME UNKNOWN` states, tab/window hiding, read error, project revision change, or workspace unmount. On returning to a visible active-job view, it rereads once. A failed read retains an explicitly dated previous snapshot where one exists and requires a manual retry.

The displayed **Local receipts read** timestamp means only that this workstation read the local database. It is not a Driver Host observation timestamp and does **not** assert that the native job actually advanced, finished or was cancelled. The canonical driver must emit further authoritative observations through the sanctioned production boundary before Motionwright can reflect a new state. Browser demo mode leaves the refresh action disabled and does not fabricate jobs.

Regression: the Studio tests cover the schedule policy, empty browser evidence and the manual refresh UI. CI verifies the TypeScript and browser behavior. This is not a substitute for independent end-to-end video quality and job-state acceptance.
