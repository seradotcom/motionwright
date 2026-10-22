# Native apply receipt completion

An apply receipt is a historical result, not permission to publish a new change.
The Native SDK adapter uses the same `StudioService::apply` transaction and receipt
validation as Studio. It does not maintain a second application state or retry loop.

A verified result returns `Completion::Applied`, including the original revision
when the service reports a replay of a known committed request. A new stale request
or a conflicting reuse of its payload is rejected through the existing error contract.

`StorageError::InvalidApplyReceipt` means that the original result cannot be
validated. The original operation may already have committed. The adapter therefore
returns `Completion::Uncertain`, preserving `outcome_known=false` through the pinned
Semwright SDK. The public error is the constant `BackendFailed` message
`Motionwright operation receipt validation failed`; stored content and detailed receipt errors are not exposed.
No receipt, project row, event, or request identifier is rewritten to obtain an answer.

Recovery must inspect retained request/result evidence and current state under the
normal authorization boundary. An uncertain result is not permission for automatic
re-execution, and current state alone is not an acknowledgement of the original call.

The `apply_receipt_completion_tests` module sends actual owned SQLite/service results
through the same conversion used by `ApplyHandler`. It covers new commits, valid
historical results, three corrupt-result forms, stale requests, conflicting intent,
row preservation, and error redaction. These unit regressions do not independently
establish Driver Host grant enforcement or an installed-application workflow; those
remain the responsibility of the existing Native SDK and real-host checks.
