# Exclusive publication of portable backups

Portable export prepares and verifies a complete bundle before publishing its directory. The destination must remain unclaimed until the publication operation succeeds. A directory or file created by another actor during export belongs to that actor and must remain unchanged.

## Requirement and behavior

This supports CR-PROJECT-06 by preserving a consistent exported snapshot at its admitted destination. It preserves the existing bundle format, snapshot revision check, project generation rules, manifest fingerprint, journal and content-addressed source bytes. Destination path and type validation supplies related hardening for CR-SEC-02; its import traversal, active-SVG and special-file acceptance assertions remain separate.

Publication keeps the staging directory and the destination parent open throughout the copy. Copied assets and the manifest are read back from the files that received the bytes. Desktop obtains a fresh owner permit before the exclusive rename, after content verification. If the owner closes during preparation, the export withholds publication and the consumed delivery grant cannot be reused.

- Linux and macOS create a private staging directory and use descriptor-relative file creation and \`renameat_with\` with \`NOREPLACE\`. Export requires a parent that protects staging entries against removal by other accounts. The original parent and staging entry are rechecked before publication. Cleanup traverses the held private directory without following links.
- Windows requires a local volume that supports persistent ACLs. It holds the parent and its ancestors without delete sharing, creates the staging directory with a protected ACL for the current user and SYSTEM, and verifies that ACL from the retained handle. \`SetFileInformationByHandle\` renames the same directory. Checked UTF-16 preserves Unicode and long paths.
- Other targets return an unsupported-operation error. A filesystem rejection leaves the destination unmodified.

## Regression and integration oracles

The normal exporter and tests share the actual capture, manifest write, publication and cleanup path. A private synchronization seam allows a peer thread to claim the destination after the full bundle is staged and before the publication operation.

`bundle_export_rejects_empty_destination_created_at_publication` requires rejection and an empty, unchanged peer directory. On Unix it also checks the original device and inode. The fixture records the exact owned staging path and preserves a separate export sentinel. The source SQLite project, journal, receipts and original CAS bytes must remain unchanged.

Additional controls exercise a late peer file, preexisting files and directories, a late dangling Unix symlink, and complete export/inspection/import/reopen with the original immutable bytes. Windows controls exercise an ordinary Unicode path longer than 260 characters and interior-NUL rejection before any move. These tests have no automatic retry or privilege-dependent silent success.

The \`bundle_export\` selector runs the late-destination, directory identity, normal restore and Desktop publication-permit controls across Linux, macOS and Windows. A test run must identify its exact checkout, selected test names, platform and outcomes. The Unix symlink test is not a Windows execution. A source review or successful compilation does not establish that a platform's publication test ran.

## Limits

Processes running as the same operating-system identity share the caller's rights. A renamed Unix staging directory may remain empty because cleanup removes its original contents without searching for the new name. The rename targets the held parent even if the parent is moved concurrently.

File content synchronization and successful process-level tests do not prove recovery from physical power loss. Portable CAS publication and SQLite import crash atomicity remain separate from export destination exclusion.

The supported public APIs and serialized bundle format are unchanged. Import still creates a new project generation while retaining the exported project ID, revision, document and journal.

## Platform references

- [Rust rename semantics](https://doc.rust-lang.org/std/fs/fn.rename.html)
- [rustix 1.1.6 renameat_with](https://docs.rs/rustix/1.1.6/rustix/fs/fn.renameat_with.html)
- [Microsoft SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)
