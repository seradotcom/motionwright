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

## Post-trigger transaction authority

Receipt insertion is private to an owning SQLite transaction. After all extension
triggers finish, the operation must still represent exactly the requested append.
A streaming SHA-256 fingerprint, count and last ordinal cover every preexisting
receipt in the current project, across providers, request keys and generations.
The fingerprint uses persisted string fields, including the original payload
JSON text, with explicit field-tuple lengths and ordinal boundaries.

Excluding only the requested new UUID, the final fingerprint must equal the
initial one. The new row and mapping must exist unchanged, its ordinal must
follow retained history, and the loaded Project must equal the value admitted
before insertion. A recovery chain must end in exactly the newly inserted
receipt. A failed postcondition rolls back the receipt, nested writes and any
audit writes made by that statement.

This rejects a trigger that inserts a recovery terminal or a new dispatch,
changes a historical proof while leaving its state superficially valid, or
changes project authority during admission. A trigger that only records an
ordinary extension audit row remains supported in either creation order.

Fingerprinting uses memory proportional to the largest serialized receipt,
with two linear history reads. It protects the current project's persisted
receipts and loaded Project; it is not confinement of arbitrary SQL extensions
across unrelated projects or an OS-level integrity guarantee.

## Service and SDK consumers

The service first validates the complete newest-first receipt snapshot against
its watermark and count, then reverses it once for ascending commit-order job
projection. Projection never sorts observation authority by a display timestamp
or UUID. The latest accepted observation also determines the job's list position;
its original timestamp remains unchanged.

Original-start binding, provider generation, ambiguous lineage and terminal
state rules remain in force. A later failed status observation cannot be hidden
by an earlier successful observation whose display date is in the future.

The Native SDK preserves its public UUID cursor shape. It delegates cursor
membership and ordinal bounds to storage, including a valid continuation whose
UUID is greater than the newest receipt's UUID. Both service and SDK reject
unresolvable cursors; an empty history cannot silently accept an anchor.

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
A supported schema 3 must have exactly one canonical object for each name:
a valid table or index cannot hide a same-name extension trigger, nor can the
mapping trigger hide a same-name extension table. Earlier schemas require zero
reserved objects before migration. Existing schema 3 mappings are validated
rather than silently rebuilt. Missing,
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
| CR-JOBS-06 | Complete local receipt snapshots remain ordered and changes require resynchronization | 270-row service and SDK pages preserve every retained receipt; stale watermarks are refused; external event-stream guarantees remain separate |
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

The service regression source a8ee3bf5ce5b138d7c3a2f65d1603a66a26b5f53
reproduced three failures in
[run 39508207967](https://github.com/seradotcom/motionwright/actions/runs/39508207967):
a stale Running state replaced the later uncertain observation, jobs were ranked
by their future display dates, and an empty history accepted an unresolvable
cursor. The fourth test passed its complete 270-row snapshot, foreign/missing
anchor and stale-watermark controls. No tests in that four-case suite were ignored.

Native SDK regression source 160220b2e9c611a027fbde9ce1f226396108b38d adds
real SQLite-backed pagination across the same clock inversion and a paginated
job-order oracle. Its test-only rusqlite dependency reuses the existing workspace
package; no product storage mutation interface is exported. Both new cases
failed in [run 39508925559](https://github.com/seradotcom/motionwright/actions/runs/39508925559):
143 existing native unit tests passed, two failed, zero were ignored. The valid
continuation was rejected by UUID comparison and the older job appeared first.

The post-trigger and schema-name test-only source
7f472aab9ce83bec005b64a3e4bba0df868379a1 reproduced ten failures in
[run 39510360779](https://github.com/seradotcom/motionwright/actions/runs/39510360779):
25 production storage tests passed, ten failed and none were ignored. Both
trigger creation orders returned an inadmissible claim; the retained-v2 order
left a retryable failure as its canonical head. Historical proof, nested keys,
project revision and all three reserved-name collisions also failed their
negative assertions. The ordinary audit-only extension control passed.

The corrected source must run the identical regression bodies, all schema
migration tests, applicable service/native consumers and strict linting:

    cargo test --locked -p motionwright-storage production
    cargo test --locked -p motionwright-storage --test schema_migration_atomicity
    cargo test --locked -p motionwright-storage -p motionwright-service --all-targets
    cargo test --locked -p motionwright-native --all-targets
    cargo clippy --locked -p motionwright-storage -p motionwright-service -p motionwright-native --all-targets -- -D warnings

## Integration obligations and evidence limits

Installed startup validation must expect schema 3, and exact test rosters must
include the new migration cases. Source-only review does not satisfy installed
consumer gates. Current binary validation and platform runs must retain their
own source identity; older results cannot be transferred to the repaired SHA.

Each runtime result must identify source SHA, run, job, attempt, selected test
names, counts and omissions. Migration error rollback does not prove power-loss
durability or binary identity of a database whose journal mode changed.
Reopen and VACUUM controls do not establish installed usability, authentic
provider authorization, the cause of issue #49, or a stable release.
