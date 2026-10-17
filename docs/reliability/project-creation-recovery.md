# Durable project creation and read-only recovery

## Contract

A project creation request has an explicit ProjectCreationIntent:

- request_id: a non-nil UUID retained before the first dispatch.
- title: the exact validated original input, without trimming or normalization.

The application must authorize this intent against its trusted workspace and
invoking window. The DTO is not a consent token. Caller-provided database paths,
window labels or session identifiers do not establish authority.

StudioService::create_project_with_intent and the matching Store method return
ProjectCreationOutcome { request_id, project, replayed }. The project is the
original revision-zero document. Independent requests may use the same title.
Reusing one operation UUID with a different title returns RequestReuse.

The existing title-only creation API remains available for existing callers.
Recoverable application flows must deliberately adopt the intent API; merely
calling the old method twice cannot preserve a request identity.

## Atomic persistence

One SQLite Immediate transaction reads any existing receipt and, for a new
request, inserts the project and a typed creation receipt in the existing
receipts table. Revision zero has no Change event. A failed receipt insertion
rolls back the project as well.

The project UUID uses a fixed, versioned SHA-256 namespace and the operation UUID,
encoded as UUIDv8. The title does not affect this ID. The receipt key has its own
versioned purpose prefix; legacy Change receipts using that prefix remain valid
for their original operations.

The request digest binds purpose, version, operation UUID and exact title.
The versioned receipt retains the original project JSON bytes and their SHA-256.
It validates those bytes before parsing the Project. This avoids invalidating an
authentic historical receipt when additive domain defaults are materialized by a
future reader. Duplicate envelope fields and unsupported receipt versions fail
closed. Creation receipts are not fabricated ApplyOutcome mutations.

A replay checks the original identity, revision, title and integrity, and the
current project's authoritative SQL columns and generation. An unrelated project
at the derived ID is never adopted or overwritten. A creation receipt used as an
import or Change request produces a purpose mismatch, not a second mutation.

## Lost response

Persist the exact intent before dispatch. If the response becomes uncertain,
retain that same intent and call recover_project_creation:

- Some(outcome) is the original committed result, returned with replayed=true.
- None means that no matching receipt was found. It does not authorize a retry.
- An inconsistent receipt or current document is an explicit integrity error.
- A current generation differing from the original result is a generation error.

Recovery uses one Deferred read snapshot and does not create, repair, normalize,
advance or retry anything. The original revision-zero result can coexist with a
later edited project. Fetch the current project separately before selecting or
editing it in the application.

The receipt is scoped to the selected Store. The application's permission
registry must reject foreign workspace, window, effect and intent contexts before
creation. The service primitives do not replace that authorization boundary.

## Limits that remain explicit

Deletion uses the existing cascading foreign key, so deleting a project removes
its creation receipt. The remaining tables cannot distinguish never-dispatched
creation from creation followed by deletion. Durable deduplication beyond that
deletion would require separately retained operation evidence; no tombstone or
new schema is introduced here.

Backup/import excludes receipts and rotates generation. Recovery must not adopt
the imported project; creation with the old intent encounters an existing project.
An absent receipt after restore or deletion remains an uncertain result.

The SQLite regression models a failed receipt write and a response withheld
after commit. It is not a physical power-cut experiment or a claim of installed
application transport testing. Client checkpoint fixtures exercise the service
contract; application permission, persistent pending state and UI wiring require
their own integration evidence.

## Capability and regression mapping

| Capability | Implemented boundary | Executable evidence |
| --- | --- | --- |
| Single transaction | Project, empty initial Change journal and durable receipt commit together | Storage trigger rejects the receipt INSERT; no partial project/receipt/event remains |
| Creation idempotency | Stable operation UUID and payload-bound receipt | Service duplicate delivery, reopen and two independent connections; same UUID/changed title rejects |
| Existing naming behavior | Title is not a deduplication identity | Distinct operation UUIDs with one title produce two valid projects |
| Read-only uncertain recovery | Historical result, current generation and head remain distinct | query_only, complete logical SQL snapshots, later edit, retained client intent and reopen |
| Restore compatibility | Restore does not restore operation authority | Independent Store import rotates generation and cannot be adopted as the creation result |
| Service integration | Existing Store and StudioService APIs | Real SQLite service tests; application authorization and installed flow remain separate gates |

Run cargo test -p motionwright-storage project_creation:: and
cargo test -p motionwright-service --test project_creation_idempotency.
The before/after baseline uses the old service method through a small adapter;
the fixed adapter forwards to the intent API. The same five regression bodies
check real stored rows before replay metadata. Recovery tests exercise the new
API separately and do not pretend it existed in the baseline.
