# Durable apply receipt binding

## Required v0.1 capability

A committed project mutation must remain distinguishable from a missing,
incompatible or corrupt receipt after a lost response, later edits and process
restart. SQLite must publish the project row, its exact Change event, the
original result and the evidence linking that result to its request in one
transaction. Schema upgrades must preserve old results and must not adopt
unrelated extension columns as trusted evidence.

This contract covers durable idempotency, recovery of uncertain local
operations, transactional migrations and source-free import recovery.
Installed application authorization, Broker/provider recovery and independent
release acceptance require their own evidence.

The existing
[apply process recovery contract](storage-apply-process-recovery.md),
[file import recovery contract](file-import-recovery.md) and
[project creation contract](project-creation-recovery.md) remain applicable.

## Defect and causal regression

The original request digest hashes the project ID and canonical Change. It
intentionally does not include the request key or the expected revision, so
the same requested edit can be committed at two different revisions with two
different keys.

A reader that checks only project authority, revision adjacency and the
referenced Change digest can accept the second result transplanted into the
first receipt if its embedded key is changed to the first key. Both real
events have the same Change digest. This is a storage consistency defect,
even though the resulting project remains valid.

The test-only baseline is commit
c9929498f49bcd03101390c44b5a12f31e43f052. Its test
versioned_binding_rejects_same_change_from_a_later_execution uses two
successful Store::apply calls, alters only the first persisted result, and
expects recovery to reject it without changing SQL rows. The test body is
preserved in the repair. An expected failing assertion is a causal prediction,
not evidence that the baseline was executed; execution results must name their
exact checkout SHA.

## One writer transaction and two receipt families

Schema 4 adds these columns to the existing receipts table:

| Column | Contract |
| --- | --- |
| apply_binding_version | INTEGER NOT NULL DEFAULT 0, with a real column CHECK admitting only 0 and 1 |
| apply_binding_json | Nullable TEXT |

The existing Store::apply transaction captures the canonical project before
applying the Change. It serializes the original ApplyOutcome once, computes
the binding from that exact text and inserts the result, marker 1 and binding
in the same INSERT. Project update, event, receipt and existing cache eviction
outbox remain within the same IMMEDIATE transaction. There is no second
execution engine or follow-up binding write.

The writer checks revision addition and SQLite's signed integer range before
changing SQL state. It does not saturate or cast an overflowing revision into
an SQLite integer.

Existing generic apply receipts migrate to marker 0 / NULL. They retain their
request digest and exact result text. Full historical replay through the
existing API remains available after validation, including after later edits.
The migration and replay never synthesize a V1 binding for them.

Project creation has its existing typed revision-zero envelope and no Change
event. Its writer explicitly stores marker 0 / NULL. Its reader rejects a
nonzero generic marker or any non-NULL generic binding; creation is never
reinterpreted as an apply result.

File imports use generic ApplyOutcome receipts. Both Store::apply and
inspect_file_import use the same verifier for the raw result hash, canonical
authority, exact Change event kind, event payload digest and V1 binding.
Import-specific source digest, asset, voice and purpose checks still run
after that shared verification. Recovery does not open the original source
or CAS blob.

## V1 representation

The binding is a strict JSON object with twelve string fields:

- format, fixed to motionwright.apply-binding/1;
- project_id, generation and resource;
- request_id and request_sha256;
- base_revision and base_branch captured before the change;
- result_revision and result_branch from the committed outcome;
- result_sha256 and binding_sha256.

UUID fields use non-nil canonical lowercase hyphenated UUIDs. Revisions use
canonical unsigned decimal strings without leading zeros and must fit
SQLite's signed integer range. Values above JavaScript's exact numeric range
remain strings. This storage representation does not authorize an existing
numeric transport to pass such values without a separate protocol review.

The original request digest algorithm is unchanged. result_sha256 is SHA-256
of the exact UTF-8 result_json stored in the row, before deserialization.
Reordering JSON, whitespace or a changed valid title therefore changes the
digest. A reader must not reserialize a historical result to check this hash.

binding_sha256 is SHA-256 over the UTF-8 domain
motionwright.apply-binding/1 followed by one NUL byte, followed by the compact
JSON serialization of this ordered ten-string array:

1. project_id
2. generation
3. resource
4. request_id
5. request_sha256
6. base_revision
7. base_branch
8. result_revision
9. result_branch
10. result_sha256

The format discriminator and checksum itself are not array entries. The
domain identifies this algorithm. Duplicate or unknown JSON fields,
noncanonical decimals, malformed digests, invalid SQLite column types,
invalid UTF-8, missing V1 data and oversized bindings are rejected.
The maximum stored binding is 4096 bytes. SQL checks its byte length before
returning its text to Rust. Presence is retained even when the text is too
large, so marker 0 plus oversized data cannot degrade to a legacy receipt.

A binding is consistency evidence produced by the transactional writer. It
does not authenticate a database against an actor able to rewrite the project,
event, result and all checksums coherently. It is neither an execution grant
nor proof of an external provider's effect.

## Read-only observation on an admitted Store

observe_apply_receipt_v1 uses one DEFERRED transaction on an already admitted
Store. It checks the current project authority and reads the receipt and event
on that snapshot. It does not call Store::open, migrate, run cache cleanup,
read source/CAS bytes, consume a grant, call a provider or call apply.

| Observation | Meaning |
| --- | --- |
| KnownVerifiedV1 | The stored local result has a valid V1 binding and matches the supplied request under the current project authority |
| LegacyUnverified | A valid historical generic result exists without V1 evidence |
| NotFound | No receipt row for this key exists in the admitted authority snapshot |
| Invalid / RequestMismatch | The stored request digest differs from the supplied request |
| Invalid / ReceiptIntegrity | Stored content or its relationships fail validation |
| Invalid / UnsupportedContract | The request encoding, row marker or binding format is unsupported |
| AuthorityMismatch | The current project is absent or the resource/generation differs |

Only KnownVerifiedV1 establishes this local commit observation. Every other
state leaves an uncertain operation unresolved; none permits automatic
resubmission, a new request key or a claimed failure of an external effect.
SQLite busy, I/O and statement failures remain errors, never NotFound.
Primitive column conversion failures are classified as invalid content.

A later replay attempt may legitimately have supplied a newer expected
revision than the original execution. Observation therefore verifies resource
and generation, then returns the original captured base and result. It does
not reconstruct the original base revision or branch from the current head.
A real branch switch may have different base and result branches, and the
current active branch may subsequently change again.

The public observation is bounded; tests serialize its complete tagged
representation using maximum-length escaped and Unicode request keys.
Validation still reads and validates full current/historical project
documents and therefore costs O(document). This work makes no constant-memory,
paged-read, wall-time or installed-transport claim.

## Schema admission and atomic migration

The two new names are reserved. Versions 0 through 3 containing either name,
including case variants and hidden/generated columns, are rejected.
Schema 4 requires both ordinary columns with the exact admitted types,
nullability, defaults and marker constraint. Future schema versions fail
closed. Existing additional unrelated data is preserved.

Admission runs before switching journal mode and again under the existing
schema writer lock. Both ALTER statements and the authoritative user_version
stamp share that transaction. No read-only layout check executes a probing
INSERT.

The marker constraint is recognized from actual table DDL with a bounded
lexer. Comments, literals, defaults, constraints on another column and
permissive expressions cannot supply the required column constraint.
Spaces, comments between tokens, quoted identifiers, outer parentheses and
the reversed numeric list are accepted. Other expressions, including
potentially equivalent expressions, are outside this admitted schema format.

The table DDL inspection limits are 256 KiB, 32768 tokens and parenthesis
depth 64. They are schema admission bounds, separate from the 4 KiB binding
limit.

The integration fixtures reconstruct the source-equivalent schema 3
representation while preserving actual committed receipt and event bytes.
They are not evidence from a previously compiled application. Two existing
schema-2 fixture builders also remove the new columns before assigning
version 2; their assertions and production behavior are unchanged.

## Regression and integration evidence

Execution status belongs to the exact candidate SHA and original CI output,
not this document or a prior green run.

| Requirement | Concrete oracle |
| --- | --- |
| Request/result integrity | Two real identical Change executions and transplanted result; valid result title/whitespace corruption |
| Historical recovery | Lost response on later replay, reopen, changed head and real branch switches |
| No implicit retry or repair | Full SQL snapshots and query-only observer connection |
| Import authority | Same verifier rejects corrupt imported result and wrong event kind; recovery succeeds without source/CAS |
| Typed creation isolation | Both creation recovery and replay reject generic marker confusion |
| Exact wire values | Revisions above 2^53, SQLite overflow rejection, resealed noncanonical decimals and maximum request keys |
| Migration preservation | Both old receipt families, raw result text, old request hashes and unrelated extension data |
| Fail-closed admission | Physical database/sidecar/tree equality after rejected layouts, hidden names and future versions |
| Migration atomicity | Real unit-test subprocess exits without destructors after each ALTER; direct read-only SQLite snapshot before any product reopen |
| Apply atomicity | Existing four process termination cases now capture both binding columns and verify the committed binding facts |

The original twelve apply receipt integrity tests remain unchanged. The new
integration suites are apply_receipt_binding (13 cases) and
apply_binding_migration (6 cases); the binding module has 7 unit cases.
The existing four apply process cases have an expanded receipt oracle. A fifth
case in that same test-only process module exercises both migration checkpoints.
The source-only roster is not an executed test count.

Selected commands for the separately reviewed runner:

    cargo test --locked -p motionwright-storage --test apply_receipt_integrity
    cargo test --locked -p motionwright-storage --test apply_receipt_binding
    cargo test --locked -p motionwright-storage --test apply_binding_migration
    cargo test --locked -p motionwright-storage --lib apply_receipt_binding::tests
    cargo test --locked -p motionwright-storage --lib apply_crash_tests
    cargo test --locked -p motionwright-storage --lib file_import::tests
    cargo test --locked -p motionwright-storage --lib project_creation::tests

The full storage/service integration gate must also cover production receipt
migration, file-import idempotency, project-creation idempotency and the
existing project projection contract. Linux/macOS/Windows results must be
attributed separately. Physical power loss, installed Broker/Host/SDK recovery,
platform signing and human acceptance remain outside these test claims.
