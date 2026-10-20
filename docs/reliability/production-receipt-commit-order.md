# Production receipt commit order and recovery authority

## Contract

Receipt UUIDs and observation timestamps are identities and descriptive metadata.
They do not establish transaction order. Clock correction or a new application
process can produce a later committed receipt with a lower UUID.

SQLite schema 3 records each receipt in production_receipt_order using an
INTEGER PRIMARY KEY AUTOINCREMENT ordinal. The AFTER INSERT mapping trigger runs
inside the receipt's IMMEDIATE transaction. The application does not reserve or
accept caller-supplied ordinals. The ordinal orders retained committed writes;
it is not a gapless counter, elapsed time or proof of external execution.

The original production_receipts table remains intact, including its UUIDs,
payload bytes, indexes, extension foreign keys and extension audit triggers.
Append and claim verify the inserted receipt and its mapping before commit.
An ordinal allocation failure, interrupted extension trigger or changed inserted
row causes rollback, including any partial statement effects.

Latest-by-request, recovery admission, watermark and receipt pages use this
order. Public cursor UUIDs resolve to ordinals within the same project and read
snapshot. Missing, foreign or forward cursors are errors; they cannot silently
widen a page or claim that an incomplete history is empty. The watermark remains
the head receipt UUID plus count. Its UUID may decrease lexicographically.

## Recovery attempts

Only claim_recovery_stage admits an application-owned recovery attempt.
Generic receipt append cannot create an initial recovery dispatch or an
unadmitted terminal result.

A shared recovery history validator checks the complete immutable request
binding, admitted attempt numbers and transitions in retained order:

| Prior state | Allowed next event |
| --- | --- |
| No recovery history | Claim dispatching attempt 1 |
| Dispatching attempt N | Completed, failed_known or outcome_unknown for N |
| Motion Canvas failed_known N with retryable true, N below 16 | Claim dispatching N+1 |
| Completed, outcome_unknown, or a nonretryable terminal | No new claim or generic terminal reclassification |
| Ambiguous legacy history or wrong attempt | Authoritative read, append and claim fail closed |

A result from an earlier attempt cannot close a newer pending attempt. An
uncertain operation cannot become retryable merely because another generic
checkpoint says failed_known. Same-key and renamed-key admissions cannot evade
an unresolved operation of the same provider and generation. Blender retains
its restriction against automatic retry after known mutation failure.

This is local durable admission and correlation. Semwright's Broker and Driver
Host retain external runtime authority. These rules neither execute nor
reconcile an UNKNOWN external operation.

## Migration and compatibility

The pre-WAL schema check reads version and objects from one snapshot. After
acquiring the IMMEDIATE migration lock, Store rereads the version and validates
the schema again. The new table, backfill, index, trigger and final user_version
stamp commit together. A future schema is refused.

Migration captures the current retained production_receipts rowid order.
It does not reconstruct ordering already destroyed by external maintenance or
history editing. Inconsistent recovery transitions remain visible in audit pages
but cannot supply completed proof or retry authority.

Reserved names are checked across object kinds without case sensitivity.
Existing schema 3 mappings are validated rather than silently rebuilt. Missing,
orphaned or wrong-project mappings cause an error. Normal application receipts
are immutable; external project reassignment is not adopted.

Project/domain fields, public receipt structures, backup format 1 and bundle
format 1 are unchanged. Legacy schema-2 backup digests are validated with their
recorded source schema. New exports identify the current source storage schema.
Operational receipts remain excluded from portable creative-project backups.
An older binary must not downgrade a schema-3 database.

## Requirement contributions and executable oracles

These tests support scoped clauses; they are not whole-criterion acceptance.

| Requirement | Contribution | Real storage oracle |
| --- | --- | --- |
| CR-JOBS-02 | Durable intent cannot be bypassed after reopen by changing a key | Pending same-key and renamed-key claims leave receipt count and watermark unchanged |
| CR-JOBS-03 | Preserve request binding and admitted attempt authority | Late, missing and future attempt checkpoints are refused; existing request-binding tests remain |
| CR-JOBS-08 | A known failed retry consumes its attempt without resetting the 16-attempt ceiling | Attempt 2 failure advances to 3; this does not establish model/render/disk budgets |
| CR-PROJECT-07 | Versioned, atomic receipt-order upgrade and future-schema refusal | NULL backfill rollback, reserved-object collision, concurrent migration and original future-version tests |
| ACC-036 | Local storage layer of lost-result recovery | UNKNOWN or ambiguous retained history never grants a new blind dispatch; installed/native acceptance is separate |

The original eight order regressions failed on test-only source
b70cc603d431539587d3f09750bfd325f00879ff in
[run 39505458296](https://github.com/seradotcom/motionwright/actions/runs/39505458296):
12 passed, eight failed, zero ignored.

The four additional attempt-authority regressions also failed on test-only source
59145cf09e8f5f02f8d80fed448bd0892f69ffb6 in
[run 39506509072](https://github.com/seradotcom/motionwright/actions/runs/39506509072):
12 passed, twelve failed, zero ignored. These fixtures use real Store calls and
isolated SQLite data; no system clock or native provider is changed.

The corrected source must run the identical regression bodies, all schema
migration tests, applicable service/native consumers and strict linting:

    cargo test --locked -p motionwright-storage production
    cargo test --locked -p motionwright-storage --test schema_migration_atomicity
    cargo test --locked -p motionwright-storage -p motionwright-service --all-targets
    cargo test --locked -p motionwright-native production::tests --all-targets
    cargo clippy --locked -p motionwright-storage -p motionwright-service -p motionwright-native --all-targets -- -D warnings

## Integration obligations and evidence limits

Job projections must preserve receipt commit order instead of re-sorting by
timestamps. SDK cursors must delegate ordinal comparisons to storage. Installed
startup validation must expect schema 3, and exact test rosters must include the
new migration cases. Source-only review does not satisfy these consumer gates.

Each runtime result must identify source SHA, run, job, attempt, selected test
names, counts and omissions. Migration error rollback does not prove power-loss
durability or binary identity of a database whose journal mode changed.
Reopen and VACUUM controls do not establish installed usability, authentic
provider authorization, the cause of issue #49, or a stable release.
