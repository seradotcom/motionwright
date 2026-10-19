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
