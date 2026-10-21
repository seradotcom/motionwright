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
outside source checkouts and worktrees. Each checkpoint creates its own new
subdirectory;
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

## Source-specific workflow modes

The process-recovery workflow keeps separate product and gate checkouts. Its
default `source` mode runs policies and the complete Python admission controls
without compiling or executing the product. Product execution requires an
explicit `workflow_dispatch` and a reviewed mode/source/platform combination.

| Mode | Product source | Platform selection | Executed product tests |
| --- | --- | --- | --- |
| `source` | Historical default input; no product checkout | `both` | None |
| `historical5fa` | `5faedc090aa45b33833b229050c09bf424de938f` | `both`, `windows` or `macos` | Four mutation interruption cases per selected OS |
| `binding4-core44` | `449de7a315a513468610d517e78f9ab87f76ae9d` | `linux` | Original causal control and 44 current tests |
| `binding4-process` | `449de7a315a513468610d517e78f9ab87f76ae9d` | `both`, `windows` or `macos` | Four mutation interruption cases and one DDL migration case per selected OS |

An unknown mode, source or platform combination fails before a product build.
The existing narrow push trigger runs G0 only. The historical command-line
default and source constants remain available to existing consumers.

For example, after selecting an exact gate revision:

```sh
gh workflow run storage-apply-process-recovery.yml --ref <gate-ref> \
  -f mode=binding4-core44 -f platforms=linux \
  -f source_sha=449de7a315a513468610d517e78f9ab87f76ae9d
```

The corresponding OS run uses `mode=binding4-process` and the required
`platforms` value. To reproduce the historical four-case source, use
`mode=historical5fa` with the full `5faedc...` commit shown in the table.
A source-only run is not evidence of either product mode.

### Linux binding4 core

The selected count is closed:

| Target | Tests |
| --- | ---: |
| `apply_receipt_integrity` integration target | 12 |
| `apply_receipt_binding` integration target | 13 |
| `apply_binding_migration` integration target | 7 |
| `apply_receipt_binding::tests` unit module | 7 |
| `apply_crash_tests` unit module | 5 |
| Total | 44 |

The last migration integration case observes legacy import recovery after its
source and CAS are unavailable. These 44 cases do not include other migration,
service, projection, creation or installed-application suites.

The workflow first checks out
`c9929498f49bcd03101390c44b5a12f31e43f052` in a separate baseline directory
and target. It compiles and lists the original one-case
`apply_receipt_binding` integration target before executing
`versioned_binding_rejects_same_change_from_a_later_execution`.
The required control is an observed exit code 101 with exactly that failed case and
the original assertion at line90:
`accepted corrupt known result: Ok(`.
Compile failures, setup errors, a different assertion, an ignored case,
unexpected success or an unknown process outcome do not satisfy this control.

The current source retains the complete original test file as a byte-identical
prefix. Its three integration targets and one library test binary are compiled
once, selected from actual compiler records, hashed and enumerated. The gate
requires every selected title exactly once with no ignored cases, retries or
missing groups. Filtered counts come from each actual binary's enumeration.
The baseline failure is recorded separately from the 44 successful current
cases; it is never counted as a current PASS.

### Six-field phase representation

The historical source retains four strings per receipt snapshot row. The
binding4 source retains six: the same request, digest, result and creation fields,
followed by the binding version and JSON representation. The gate selects this
width from the closed source cohort. Other row types retain their existing
widths and types.

The Rust snapshot query casts the version to text and coalesces SQL NULL binding
JSON to an empty string. These retained strings therefore do not independently
prove the SQL distinction between NULL and an empty string. The corresponding
typed storage assertions remain authoritative for that distinction.

### Fifth DDL case

`binding_migration_ddl_rolls_back_after_abrupt_exit` creates two owned temporary
fixtures with a source-equivalent schema3 representation. Each fixture starts
one owned child that exits with code 86 after one of the two binding-column ALTER statements.
The parent inspects the schema and original rows before any recovery through
`Store::open`, then separately verifies a successful reopen that reports the
legacy receipt as unverified.

This case retains the original parent result, exact source and executable
identity. Its child logs and databases belong to temporary fixtures and are not
independently archived. The gate runs this parent with normal libtest capture,
without `--nocapture` or `MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT`, so internal child
output cannot be mistaken for extra parent results.

For binding4 process runs, the retained artifact totals remain twenty phase JSON
files and twelve child-original records from the four mutation cases. The fifth
parent contributes no independently retained child-original or phase files.

### Attribution and incomplete observations

Every selected execution retains the original commands, stdout, stderr, exact
source/tree inputs, selected test binaries and before/after custody checks.
Current binding4 receipts use a separate schema from the historical receipt.
A timeout remains UNKNOWN, and the gate stops without retrying or assuming that
descendants were observed to terminate. Partial diagnostic logs cannot become
complete phase custody.

Compilation is bounded to 1,500 seconds per source; enumeration to 45 seconds;
selected cases and groups to 120–180 seconds. The Linux baseline-plus-core job
allows 90 minutes and each selected OS process job 45 minutes. These bounds limit
the attempt; they do not convert an incomplete attempt into a successful result.

A source revision, workflow or platform does not inherit another revision's
results. These gates provide no installed-application, physical-power-loss,
complete raw-database/CAS archive, human-acceptance or stable-release authority.
