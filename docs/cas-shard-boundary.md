# Content-addressed storage: symlinked shard boundaries

Motionwright stores immutable source media in an application-owned CAS tree under `blobs/sha256/<digest-prefix>/<sha256>` and initially stages imported bytes under `blobs/.staging`. The digest itself is validated to be canonical lowercase SHA-256 hex before addressing any file; the WebView and Native SDK cannot specify arbitrary paths through these APIs.

## Hardened filesystem behavior

The first-party Store now checks **each internal owned directory component** before writing, reading, admitting or exporting a digest-bound blob. These are the CAS root, `sha256` prefix and digest-prefix shard. A symlinked or non-directory component fails closed; a missing source shard reports an unavailable blob instead of following an alternative path.

New source ingestion uses create-new staging under a verified real staging directory. On Unix, stages are explicitly owner-private (`0600`), and that permission mode is preserved when a completed source is admitted to its canonical digest path. Shard directories are created **one component at a time** and rechecked to avoid blindly following a pre-existing directory symlink. The completed source is admitted by an **atomic create-new hard link** from staging to the canonical destination, followed by removal of the temporary link; this refuses a destination that another actor created after the initial existence check. Duplicate ingestions reuse a preexisting digest path only after its regular-file type, byte count and SHA-256 are reverified.

The same shard-path policy is applied when the Store verifies a registered asset during a creative change, reads a digest-bound blob, returns a verified blob path or exports a portable project. Portable exports continue independently verifying copied data against its source digest. The existing content-addressed format, SQLite schema and project revision/receipt semantics are unchanged.

This improvement does **not** claim to solve arbitrary hostile filesystem races at every mount point or all local operating-system ACL threats; the internal CAS root is expected to live under a protected application-data directory. It also does not expand Semwright Native SDK filesystem authority, permit user-provided path resolution, or replace native renderer sandboxing.

## Adversarial regression coverage

Rust storage tests exercise real imported media, stage/leaf/shard substitution, source reads, CAS reuse, registration and portable export. They verify that a symlinked digest-prefix directory or staging directory cannot be used as an alternative source/destination, that an already-present symlink cannot be overwritten by ingestion, and that a rejected export does not create a user destination. Unix tests inspect owner-only mode on newly ingested media, and existing import/portable-bundle end-to-end tests remain mandatory.

These are filesystem-boundary checks, not an independent product acceptance PASS. The read-only [asset integrity inspector](asset-integrity.md) reports the same verified/unreadable/unsafe conditions to operators and Native SDK agents without exposing file paths.
