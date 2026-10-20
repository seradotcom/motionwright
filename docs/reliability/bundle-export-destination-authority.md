# Exclusive publication of portable backups

Portable export prepares and verifies a complete bundle before publishing its directory. The destination must remain unclaimed until the publication operation succeeds. A directory or file created by another actor during export belongs to that actor and must remain unchanged.

## Requirement and behavior

This supports CR-PROJECT-06 by preserving a consistent exported snapshot at its admitted destination. It preserves the existing bundle format, snapshot revision check, project generation rules, manifest fingerprint, journal and content-addressed source bytes. Destination path and type validation supplies related hardening for CR-SEC-02; its import traversal, active-SVG and special-file acceptance assertions remain separate.

The earlier existence check provides an early error. It cannot authorize a later replacing rename: an independent actor can create an empty directory after that check. Publication now uses an operating-system operation that rejects an occupied destination:

- Linux and macOS use `rustix::fs::renameat_with` with `RenameFlags::NOREPLACE`.
- Windows uses `MoveFileExW` with zero flags. Source and destination are sibling names under the same previously resolved parent. Checked UTF-16 buffers preserve Unicode and long paths and reject interior NUL characters.
- Other targets return an unsupported-operation error. A filesystem rejection never falls back to a replacing rename or a copy into a visible destination.

The existing parent is resolved once before staging is created. A successful publication makes the completed directory available at its destination. A rejected publication cleans up the staging directory created for that attempt and preserves unrelated exports.

## Regression and integration oracles

The normal exporter and tests share the actual capture, manifest write, publication and cleanup path. A private synchronization seam allows a peer thread to claim the destination after the full bundle is staged and before the publication operation.

`bundle_export_rejects_empty_destination_created_at_publication` requires rejection and an empty, unchanged peer directory. On Unix it also checks the original device and inode. The fixture records the exact owned staging path and preserves a separate export sentinel. The source SQLite project, journal, receipts and original CAS bytes must remain unchanged.

Additional controls exercise a late peer file, preexisting files and directories, a late dangling Unix symlink, and complete export/inspection/import/reopen with the original immutable bytes. Windows controls exercise an ordinary Unicode path longer than 260 characters and interior-NUL rejection before any move. These tests have no automatic retry or privilege-dependent silent success.

A test run must identify its exact checkout, selected test names, platform and outcomes. The Unix symlink test is not a Windows execution. A source review or successful compilation does not establish that a platform's publication test ran.

## Limits

This boundary rejects destination replacement. It does not establish complete protection against a hostile actor replacing an ancestor or the randomly named staging directory itself. The existing cleanup still uses the owned staging pathname. Such substitution needs a separate identity or handle-based design and regression.

File content synchronization and successful process-level tests do not prove recovery from physical power loss. Portable CAS publication and SQLite import crash atomicity remain separate from export destination exclusion.

The supported public APIs and serialized bundle format are unchanged. Import still creates a new project generation while retaining the exported project ID, revision, document and journal.

## Platform references

- [Rust rename semantics](https://doc.rust-lang.org/std/fs/fn.rename.html)
- [rustix 1.1.5 renameat_with](https://docs.rs/rustix/1.1.5/rustix/fs/fn.renameat_with.html)
- [Microsoft MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
