# Project portability preconditions

## Behavior

A project export can be reviewed at revision R and dispatched after a different
connection commits R+1. The stamped export APIs check resource, generation and
revision inside the SQLite transaction that reads the document and journal.
A stale request returns Conflict. A foreign resource or generation returns
GenerationConflict. No bundle destination is created for those rejections.

The bundle exporter uses the already captured ProjectBackup when it copies CAS
members. A later edit does not cause a second project load or substitute the
newer revision. Existing backup format 1, storage schema 2, payload digests and
canonical project fields remain unchanged.

Every export also validates the same backup invariants required by import:
journal length equals the project revision, event revisions are contiguous, and
event timestamps are present. Corrupt history is rejected before returning a
backup or publishing a bundle, without repairing or changing the stored rows.

A bundle inspection returns both the normal import plan and a BundleFingerprint.
The fingerprint is SHA-256 over the domain
motionwright.bundle-inspection.v1 followed by a NUL byte and the exact raw
manifest bytes. The manifest names and hashes of every member are therefore
bound to the inspected input. Reencoding identical JSON changes this fingerprint.

Inspected import captures the bounded manifest once, compares the retained
fingerprint and parses those same bytes. A mismatch returns InvalidBackup before
any target CAS member or project is published. The importer still validates the
complete manifest, backup, member set, canonical paths, size budgets, source
digests and applicable secret checks.

After that validation, it captures every member into the existing private
file-import staging area. The shared capture reads at most the admitted size
plus one byte, rejects excess before writing it, and distinguishes an oversized
source from a shorter capture that no longer matches the manifest. It checks each
captured digest, size and applicable secret policy before publishing any member.
A changed later source discards the private captures; an earlier valid member
is not published as a side effect
of rejecting a changed member. Cleanup never removes another operation's
staging file or an object already published in shared CAS.

All portable member reads are bounded during the actual IO. Preliminary hashes,
text scans, private capture, export copies and copied-file readback cannot rely
only on a previous filesystem size check. Each stream reads at most its budget
plus one byte and rejects excess before accepting it into a copy or text buffer.
Export also checks the aggregate member budget and actual serialized manifest
size, so it cannot knowingly publish a bundle rejected for those import limits.

## APIs and caller contract

Storage and service expose these additive methods:

| Method | Required retained input | Result |
| --- | --- | --- |
| export_project_at | Project ID and original RevisionStamp | ProjectBackup from that SQLite snapshot |
| export_project_bundle_at | Project ID, original RevisionStamp and destination | Bundle containing that captured snapshot |
| inspect_project_bundle_with_fingerprint | Source bundle path | InspectedProjectBundle containing plan and fingerprint |
| import_project_bundle_inspected | Source path and original BundleFingerprint | Validated imported Project with a fresh generation |

BundleFingerprint validates a canonical lowercase SHA-256 when constructed or
deserialized. It is an input precondition. It does not authenticate the person,
grant permission, prove native consent or identify an earlier import operation.

A caller must retain the stamp and fingerprint actually presented for review.
Loading a new stamp or inspecting again immediately before dispatch changes the
input being approved. The original input must reach the effect boundary, along
with the real caller authorization required by that boundary.

The existing export and bundle APIs retain their signatures for compatibility.
Callers that need a reviewed-input guarantee must use the new methods. The
legacy bundle importer also benefits from captured-member validation.

Project creation or bundle import must not be retried because a response is
missing. These APIs add no receipt of a completed bundle import and no permission
to retry an UNKNOWN operation. An already imported Project remains ProjectExists.
Project backups do not restore operational receipts or native authority.

## Validation

The tests-only baseline aa6b888060bf7c03ca5076aec08333342d264c11 executed the
existing product APIs. Its three regressions failed in
[Project Portability run 39015645353](https://github.com/seradotcom/motionwright/actions/runs/39015645353):

| Reproduction | Observed baseline behavior | Required behavior |
| --- | --- | --- |
| Raw export after a peer commits | Revision 0 was inspected and revision 1 was exported | Reject the stale stamp |
| Bundle export after a peer commits | The later revision was published at the destination | Reject before creating the destination |
| Complete valid bundle substituted after inspection | The replacement project and its different CAS bytes were imported | Reject the retained fingerprint before publication |

Positive controls in the same tests prove that current exports work and that
the replacement bundle is valid under the original inspector and importer.
The regression bodies are preserved; only the adapters that supply the retained
preconditions change.

Additional storage and service tests cover an independent target CAS, generation
rotation, reopen and source bytes, absence of restored operational receipts,
foreign authority before missing-source IO, changed member bytes, equivalent
JSON reencoding, readonly inspection, malformed fingerprints, corrupt SQL
authority columns, a captured export after a peer edit, capture cleanup and the
manifest size bound and bounded member capture. These cases complement the
observed before/after regressions.

A second tests-only baseline, fc58b526a1d23029a7f9c408ea2ef26c32b6775c,
covers six additional failures in
[Project Portability run 39019336775](https://github.com/seradotcom/motionwright/actions/runs/39019336775).
Three cases modify real SQLite event rows: a missing event, a gap in revisions
and an empty timestamp. The original exporter returns and publishes backups
that its own real import validator rejects. Three file cases append real bytes
after a size review; the original hash, text scan and copy helpers accept work
beyond that retained budget. Their positive controls and six regression bodies
are unchanged on the corrected implementation.

An additional file-backed stream case appends 4096 bytes when the actual read
starts after a 64-byte size check. The shared transfer reads exactly 65 bytes,
returns BlobTooLarge and never accepts an oversized output buffer.

## Boundaries

SQLite snapshot consistency does not make publication across SQLite and the
filesystem a single transaction. A later destination, CAS or database failure
can leave valid unreferenced CAS bytes; the importer does not delete shared
objects to simulate a rollback.

Filesystem checks and staging use paths. They do not establish full ancestor
ownership, handle-based protection against every concurrent filesystem
replacement, or an atomic no-replace directory rename on every platform.
A failed export attempts to remove only its own newly created staging
directory. This cleanup is best effort if the filesystem itself refuses removal;
it does not remove unrelated staging directories or shared CAS objects.

This backend contract does not demonstrate an installed desktop caller, abrupt
process termination, physical power loss, native execution, authentic consent,
platform signing or a stable release. Those require their respective
integration evidence.
