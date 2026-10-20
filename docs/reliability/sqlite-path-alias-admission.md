# SQLite path and owner-directory admission

Motionwright opens only ordinary SQLite filesystem paths for project stores. Recognizing an existing v1/v2 Motionwright schema is not authorization to follow a filesystem alias into another user's directory or another persisted database.

Before creating any parent directory or entering SQLite, storage examines the selected database leaf and every existing ancestor using `symlink_metadata`. A symbolic link, Windows reparse point, non-regular leaf, or non-directory ancestor fails closed with `UnsafeSourcePath`. The same guard runs again after any parent creation, and SQLite opens with `SQLITE_OPEN_NOFOLLOW`, protecting the final leaf at the actual connection boundary. SQLite `file:` URI filenames are not accepted as project path aliases.

On macOS, only two exact system root aliases (`/var` -> `/private/var`, `/tmp` -> `/private/tmp`) can be admitted after canonicalizing and checking the precise expected destination; arbitrary aliases under these roots or other selected parent directories are still rejected.

The original Linux test-only RED source `6bc232508fc844083ae4f03dc6a533da4ef3db02` reproduces three unsafe paths with real temporary SQLite files and symlinks: final-leaf alias to a valid but foreign existing Motionwright store, redirected parent creating an unrelated database, and a symlink ancestor concealed under a regular child directory. It requires no project, journal, WAL or foreign file modification when rejected. The ordinary creation/reopen positive remains supported.

The Unix regression module preserves the original failing cases unchanged. Validation of a candidate must include locked storage and service Rust tests, strict Clippy, real macOS symlink cases, and Windows NTFS file and directory reparse cases on that source. A test which cannot create the relevant link provides no evidence for that case.

Limits: these checks prevent stable alias paths and final SQLite symlink substitution where supported, but they are not an OS directory-handle capability. A same-user malicious actor able to change *ancestor* directory entries between checks and open, or an undetectable hardlink to an otherwise valid SQLite file, remains a separate risk. SQLite/CAS cross-resource crash atomicity and the corresponding exact-source process-cut evidence also remain open. Never delete a foreign file to make a rejected path admissible. This change does not authorize automatic mutation retry, accept human criteria, or publish a stable release.
