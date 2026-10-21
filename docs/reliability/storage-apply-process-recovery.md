# Store.apply process recovery

The storage subprocess tests exercise the real mutation transaction and the
revision-triggered derived-cache eviction outbox. They close the gap between
testing equivalent SQL statements and interrupting the product operation itself.

## Observable boundaries

Each case creates its own project, scene and derived proxy through the real
storage APIs, then closes the store. A new writer process executes Store.apply
and terminates without running Rust or SQLite destructors at one named boundary.

| Boundary | Durable state after a separate process opens SQLite |
| --- | --- |
| After the project UPDATE and its trigger | Original document, event history, receipts and live cache registration; no new eviction |
| After the event INSERT | The same complete original state |
| After the receipt INSERT, before commit | The same complete original state |
| After commit, before physical cleanup | New document, one new event and receipt, revoked cache registration and a durable pending eviction |

A unique request marker and exact process exit code must both prove that the
writer reached the selected boundary. A timeout, panic or startup failure is a
test failure. Only subprocesses created by the fixture can be stopped.

The observer is another process. It lets SQLite recover its journal, enables
query-only inspection and checks integrity without calling Store.open. This
preserves visibility of the committed eviction outbox before application recovery
drains it. The original derived manifest and immutable CAS bytes are inspected
directly.

## Recovery and replay

After the independent observation establishes the durable outcome, Store.open
must recover any committed eviction. A rolled-back operation preserves its
original live cache registration. Immutable CAS bytes survive both cases.

The test then submits the original request key, payload and original revision
stamp. A previously committed operation returns the original result as a replay;
a confirmed rolled-back operation commits once. Another store reopening and
replay must leave the document, event history, receipt and cleaned outbox
unchanged. Recovery must not produce a second revision or destroy source bytes.

The four tests are named by their actual interruption boundary:

- real_apply_exit_after_project_update_rolls_back_document_and_eviction_outbox
- real_apply_exit_after_event_insert_rolls_back_the_full_mutation
- real_apply_exit_before_commit_rolls_back_receipt_and_journal
- real_apply_exit_after_commit_recovers_outbox_and_replays_original_result

Run the storage unit-test module with cargo test --locked -p motionwright-storage
--lib apply_crash_tests. No helper test is ignored; each named case runs its own
writer and observer processes.

## Scope

The checkpoints and environment handling exist only under cfg(test). They are
absent from the distributed library and do not introduce a public mutation or
termination API.

This verifies process termination, SQLite recovery, local idempotency and the
specific derived-cache eviction outbox participating in Store.apply. It does
not simulate physical power loss, defective storage hardware, portable import
publication, concurrent writer admission or atomicity with external provider
effects. Those capabilities require their own independently attributable tests.

## Optional phase evidence

Set MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT to an existing, absolute evidence
container before selecting the four tests. Use a unique container for each run,
outside source checkouts and worktrees. A repository's private knowledge/runtime
area is also accepted. Each checkpoint creates its own new subdirectory;
an existing checkpoint destination is refused without replacing its contents.
The variable is removed from each child process. Without the variable, the
original temporary-fixture lifecycle remains unchanged.

The parent retains the original case identity and serializes the exact Snapshot
values at five observation points: before, interrupted, cleaned, after_retry and
final. Each capture occurs before the assertions following that observation and
before a later recovery step can remove a derived manifest. Snapshots include
the SQL document, journal, receipts, cache registrations and eviction outbox,
plus the observed manifest and immutable blob bytes.

Every writer and observer's original stdout and stderr and terminal metadata are
retained before exit/marker/stderr assertions. The metadata distinguishes a
normal exit, spawn or wait error, and an owned-process timeout. The test never
resends an external provider operation.

A custody.json manifest lists actual retained files with byte counts and
SHA-256, captured and unavailable phases, and explicit capture errors. It is
written on successful completion and attempted during assertion unwinding.
Missing observations are unavailable; they are never replaced by expected
values. A successful test requires a complete manifest when evidence is enabled.
The caller retains the unique container even when the parent exits with failure.

This option does not archive the raw SQLite files or the complete CAS. It does
not turn a source-equivalence comparison into a new execution, or change the
four original test oracles. Runner identity, exact source/tree/lock, toolchain,
test binary and original parent output remain the surrounding runner's
responsibility. Earlier executions without the option keep their original
assertion evidence; they do not acquire retrospective phase files.
