# Portable bundle destination integrity

A portable project import validates the original manifest, its source files,
the project backup and exact inspected fingerprint before publishing any
content-addressed assets.

Before publishing the first captured member, the importer checks **every**
destination CAS path that already exists. An existing regular file must match
the captured member's exact SHA-256 and length. Symlinks, nonregular paths,
and incompatible existing files cause the import to fail before publication.
Previously stored bytes are never overwritten or deleted to satisfy a bundle.

This avoids orphaning an earlier member when a **later, pre-existing**
destination blob is corrupt or conflicts. The rejected attempt leaves the
SQLite project, journal, prior CAS files and foreign destination bytes
unchanged; temporary captures for the attempt are disposed. A complete
import with matching pre-existing CAS content remains supported.

The CAS destination checks protect against conflicts **visible during
preflight**. Publication now holds the destination SQLite IMMEDIATE writer
transaction through the project and journal commit, preventing a competing
SQLite writer from claiming the project ID after the final database check.
This is **not** a transaction across SQLite and the filesystem. A competing
CAS writer can still race after CAS preflight, publication can partially
succeed before an I/O error, and a crash or failed SQLite commit can leave
unreferenced CAS members. Recovery or cleanup requires separate evidence;
this implementation never deletes foreign CAS members, replays UNKNOWN
requests or claims whole-bundle crash atomicity.

Run the source-bound storage/service integration and selected platform
import checks from `.github/workflows/project-portability.yml`. The
`existing_later_cas_conflict_must_not_publish_unreferenced_first_member`
test reproduces a two-member late-target conflict with an unchanged SQLite
snapshot and foreign sentinel; the matching-existing-blob test covers
successful import without replacing the original CAS bytes.


## Destination project ID collision during capture

The first manifest validation rejects a duplicate destination project ID.
A second SQLite connection can still create that ID while the importer
captures verified source media. After capturing and preflighting all source members, the importer opens a
SQLite IMMEDIATE write transaction, checks the destination project ID **inside
that transaction**, publishes the captured CAS members without reopening the
source, writes the project and journal on that same transaction, and commits.
A competing SQLite writer must wait or receive BUSY; if a project ID was
already claimed before the lock, no captured CAS member is published.

The deterministic regression
newly_visible_existing_project_id_must_not_publish_any_bundle_blobs
performs an initial valid bundle inspection, creates a different persisted
project under that same ID through a second SQLite connection, and resumes
the prevalidated importer. It requires a truthful ProjectExists error,
unchanged competing SQLite rows/journal/receipts, no new CAS publication
and cleaned private capture files. The normal import and the prior
destination-CAS conflict tests must remain passing.

A second deterministic test,
`late_sqlite_writer_cannot_claim_project_during_portable_publication`,
attempts a competing SQLite insertion at the former gap after the final
read-only precheck but before the first CAS member. The initial code permits
that insertion, publishes blobs, then rejects the import; the changed code
serializes the competing writer until the import commits. The test also
preserves an unrelated private staging file and verifies the original source
members against the imported CAS bytes.

This is a database writer-serialization repair only. It does not guarantee
crash-safe cross-resource atomicity, eliminate concurrent foreign CAS writes,
provide CAS garbage collection, or authorize uncertain retries/deletions.
