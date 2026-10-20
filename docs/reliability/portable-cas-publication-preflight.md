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

The checks protect against conflicts **visible during preflight**. The
filesystem and SQLite database do not share a single transaction: another
process can race after preflight, a host can stop during publication, or the
database import can fail later. Those cases must retain their original
outcome and use a separately verified recovery mechanism. This change
does not claim whole-bundle crash atomicity, erase unreferenced blobs,
replay uncertain requests, or authorize deletion of external media.

Run the source-bound storage/service integration and selected platform
import checks from `.github/workflows/project-portability.yml`. The
`existing_later_cas_conflict_must_not_publish_unreferenced_first_member`
test reproduces a two-member late-target conflict with an unchanged SQLite
snapshot and foreign sentinel; the matching-existing-blob test covers
successful import without replacing the original CAS bytes.


## Destination project ID collision during capture

The first manifest validation rejects a duplicate destination project ID.
A second SQLite connection can still create that ID while the importer
captures verified source media. The final publication preflight therefore
also repeats the read-only database import check after all source members
are captured and before publishing the first CAS member.

The deterministic regression
newly_visible_existing_project_id_must_not_publish_any_bundle_blobs
performs an initial valid bundle inspection, creates a different persisted
project under that same ID through a second SQLite connection, and resumes
the prevalidated importer. It requires a truthful ProjectExists error,
unchanged competing SQLite rows/journal/receipts, no new CAS publication
and cleaned private capture files. The normal import and the prior
destination-CAS conflict tests must remain passing.

This guards against a project collision that is already visible before
publication. It does not eliminate a new collision arriving after the
last check, guarantee crash-safe cross-resource atomicity, provide CAS
garbage collection, or authorize uncertain operation retries/deletions.
