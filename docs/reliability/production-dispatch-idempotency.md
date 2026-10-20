# Production dispatch idempotency

Independent clients can observe the same earlier state before either records a
dispatch. A preflight read alone cannot authorize an external operation: the
storage admission decision belongs inside the write transaction.

## Admission rules

Production receipts bind a project-scoped request ID to one command, request
SHA-256 digest, project generation, and original revision.

- Reusing an existing request ID with a different command, digest, generation,
  or revision is rejected. This includes a current revision reached after the
  original dispatch; being newer does not authorize reattributing an old result.
- A new `dispatching` receipt requires a previously unused request ID. An earlier
  receipt consumes that dispatch opportunity, including when its latest state is
  `completed`, `failed_known`, or `outcome_unknown`.
- Matching observations of the original request can still be appended. A delayed
  result retains its original generation and revision when the project has moved
  to a newer revision.
- A corrupted stored project, a mismatched generation, or a future revision
  prevents receipt admission.

`append_production_receipt` begins an immediate SQLite transaction before reading,
deserializing, and validating the stored project. It checks the request binding
and dispatch eligibility, inserts the receipt, and commits before returning it.
Concurrent writers therefore evaluate eligibility against serialized database
state. A rejected claim returns `StorageError::RequestReuse` without inserting a
new receipt.

The caller must obtain a committed dispatch receipt before invoking the external
operation. The native coordinator already enforces this order and maps a rejected
claim to a conflict before calling the native client. Native execution takes
place after the transaction has ended.

The change does not require a schema migration. The receipt stream remains
append-only, so dispatch intent and later observations can share one request key.

## Unknown outcomes and recovery

`outcome_unknown` means the original operation lacks sufficient terminal evidence.
Reopening the database or repeating preflight does not create another dispatch
opportunity for its request ID.

Keep the original request identity and use the supported observation or recovery
path. A replacement key is not evidence that repeating an uncertain effect is
safe. Successful receipt persistence is also not proof that a provider completed
the operation.

Recovery-stage claims retain their existing policy and retry contract through
`claim_recovery_stage`. Existing receipt history is preserved; this admission
guard cannot undo external effects that preceded it. A permitted retry retains the
original request binding. If the project moved to a different revision, the same
request key cannot be reclaimed for that new base. Existing inconsistent receipt
histories fail closed without being rewritten.

## Verification

The regression suites exercise concurrent SQLite writers, conflicting request
bindings, duplicate dispatch admission, independent service instances, database
reopening, corrupted project data, generation rejection with rollback, and delayed
original results.

The concurrency fixture holds a real SQLite write lock and waits for both
connections to enter SQLite's busy callback before releasing them. It requires
exactly one persisted winner and one request conflict; a busy error is not an
acceptable substitute.

Run the relevant suites with:

```sh
cargo test --locked -p motionwright-storage production::tests
cargo test --locked -p motionwright-service --test production_dispatch_idempotency
cargo test --locked -p motionwright-service --test production_request_revision_authority
```

The tests live in [storage receipt tests](../../crates/storage/src/production.rs)
and the service regressions for
[dispatch idempotency](../../crates/service/tests/production_dispatch_idempotency.rs)
and [original revision authority](../../crates/service/tests/production_request_revision_authority.rs).

These tests establish local receipt admission and persistence behavior. Their
scope does not include native provider execution, recovery of historical unknown
jobs, or an atomic transaction spanning project changes, the journal, the outbox,
and external effects.
