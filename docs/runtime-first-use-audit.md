# Motionwright v0.1.0 — independent owner runtime first-use audit

This is a **separate read-only troubleshooting and preflight tool**, not a
Semwright runtime installer or an authorization path. The installed desktop UI
already has **Integrations → Choose Semwright connection** and a trusted
runtime status probe (PR #118). This source-tree Python audit is available
for owners and release reviewers who need a reproducible local report
before investigating a first native render.

## Invoking on the owner machine

Use Python 3.13 from a Motionwright source checkout, never paste a private
connection JSON or session into an issue, CI log or chat:

    python tooling/runtime_first_use_audit.py \
      --project-id <canonical-project-uuid> \
      --generation <canonical-generation-uuid> \
      --connection-file <absolute-owner-connection.json>

To inspect the selected file **remembered by the installed app** rather than
a separate explicit file, replace --connection-file with:

    --registry-root <absolute-app-data/owner-native-connections>

The registry is scoped to project_id and generation. It stores only the
original connection filename. Neither this audit nor the installed app
silently falls back to an environment connection when a saved reference is
invalid or stale.

By default, the audit executes **no external program**, reads only bounded
configuration files and the executable's bytes for SHA-256, and reports
FILES_VERIFIED_ONLY. You may explicitly add --probe-version to run the
owner-selected digest-checked CLI with the exact argument --version; then a
matching read-only probe reports CLI_VERSION_COMPATIBLE. The CLI is an
owner-provisioned executable, not an application-authenticated package.
Review your installation and its expected SHA-256 before opting into a probe.

Use --output <absolute-new-report.json> to write one new (never overwrite)
redacted evidence file, or omit the option to print sanitized JSON. A
pre-existing report makes the operation fail closed with a nonzero exit code.
The report includes no absolute paths, raw connection JSON, ticket, socket
identifier, session bytes, stdout diagnostics or provider grants.

## What the audit actually checks

- Exact motionwright-semwright-connection/1 schema, unique JSON keys,
  canonical project/generation UUIDs, and exact project:<UUID> resource.
- Optional app-owned reference schema, matching project and generation,
  absolute connection path, no symlinks or NTFS reparse points (including
  Windows junctions) in file/ancestor components. Python 3.11 Windows
  `st_file_attributes` is checked even without `Path.is_junction`.
- Bounded configuration and private session file, existing output directory
  and socket path, non-writable-by-others Unix filesystem modes.
- Streamed executable SHA-256, stable length/mtime during reading, and
  exact Semwright pin from the repo's SOURCE_LOCK.json.
- If explicitly opted in, bounded Semwright --version output and exact
  expected 1.0.0 version, with a five-second deadline.

Failures use fixed diagnostic codes, **never raw OS exception messages**.
All tests use synthetic temporary files; no owner sessions, real providers
or render effects are required.

## Deliberate limitations

A matching digest means only that the bytes equal the *owner-provided
expected hash*. It does **not** attest the source commit, trusted signer,
malware-free provenance, provider health or right to execute it. A CLI version
probe is not a Broker handshake. Even CLI_VERSION_COMPATIBLE does NOT mean
the renderer is ready or that real Motion Canvas / MLT operations passed.
claims.broker_ready stays NOT_TESTED, render_success and independent
human acceptance stay NOT_RUN, and release_ready remains false.

For a real owner acceptance run, separately verify the installed GUI,
Broker/Driver Host generation, current revision-scoped Semwright Native SDK
operations, readback evidence, H.264/AAC master and provenance. Do not mark
the 60 independent acceptance cases complete from this preflight or CI.

## CI and safety

runtime-first-use.yml runs the Python negative tests on GitHub-hosted
Windows, Linux and macOS runners. On Windows it also compiles/tests the
application-owned Tauri registry, including an exclusive-open sharing test:
failed replacement of an existing saved connection must **not** erase the
previous working reference. This fixes a Windows delete-before-rename
failure path without adding a new runtime or changing the Native SDK pin.

The source-only tool does not ship as a separate installer, update package
or auto-run background service. All previous owner connections, Git branches,
local assets and worktrees remain untouched.


### Platform trust boundary

On Unix, this audit additionally rejects group/world-writable connection paths
and sessions that are readable by another account. On Windows it checks regular
files, reparse/symlink paths, expected hashes and scope, but **does not**
independently audit NTFS access-control lists or prove that the owner socket
belongs to the expected Broker. Filesystem checks can be subject to concurrent
modification; the trusted Tauri backend revalidates before every operation.
Owners must still verify OS permissions and Broker policy separately.


### Open-handle identity and race checks

The original preflight validated a filename before opening it. A different regular
file could be renamed or hardlinked into place immediately afterward, supplying
scope-valid JSON or the same executable bytes from a different inode. The
auditor now compares the file's device/inode identity before and after opening,
checks the held descriptor's type, size and permitted modes, and revalidates
the complete filesystem path (including reparse/junction ancestors) while the
descriptor remains open. It rechecks the descriptor and path after bounded
JSON reads and after hashing the executable. The session token remains unread;
only metadata from its held descriptor is checked, without exporting secrets.

Three synthetic hardlink-substitution regressions cover saved connection JSON,
the executable and the app's registry reference. They failed on the old source
without needing a Broker or native render and must pass on the corrected
source on Windows, Linux and macOS. Additional after-open substitutions cover
the connection and session files. These checks prevent a *false snapshot
preflight* caused by a filename swapping between the checks.

**Scope limit:** Closing the descriptor ends this inspection's identity
protection. An independently executed `--version` process, the installed
Tauri runtime, Broker and native job authorization still have their own trust
boundaries. This is not an OS-wide file lock, not Windows DACL verification,
and not an attestation or consent mechanism. A report never licenses a later
renderer mutation.


### Exclusive, atomic support-report publication

The optional `--output` receipt is local troubleshooting evidence, not a Broker
operation. Its original writer used `O_EXCL` directly on the public destination
and could leave partially written JSON after `write()` or `fsync()` failed.
The incomplete name then blocked a later valid report because the same
destination must not be overwritten.

The corrected writer creates a unique, mode-0600 staging file in the **same
parent directory**. It writes the complete redacted JSON, flushes and fsyncs
the stage, and makes the complete file visible by an **atomic exclusive
hardlink** to the requested destination. Linking never replaces existing
reports. Publication failure preserves previous destination bytes and removes
only this operation's matching staging inode. On POSIX the parent directory
is held by descriptor for stage creation, linking, cleanup and directory
sync. The parent pathname is independently revalidated after publication;
on Windows the same-volume NTFS hardlink operation is used. If a filesystem
cannot perform this no-replace publication, the operation fails closed and
does not attempt a weaker direct-write fallback.

`receipt_write_refused` means the new public destination was not accepted.
If a publication occurred but its final durability or cleanup is uncertain,
the result is `receipt_commit_uncertain`; the caller must inspect the
original destination before deciding any subsequent action. It must **not**
overwrite an existing file or classify that uncertainty as a safe retry.
Atomic publication of one local JSON support report is not a transaction for
the installed application, provider or OS filesystem.

The synthetic test suite `test_runtime_first_use_atomic_receipt.py` proves
a failed stream sync never exposes a partial public name, the first sync
precedes public visibility, collision preserves an existing report, a
refused hardlink has no fallback, and two writers have one complete winner.
On POSIX it also tests a directory-sync failure **after** publication,
requiring an uncertain result while preserving the fully published bytes.
It executes through the selected first-use test module without changes to
A6-owned workflows.


#### Publication succeeded, metadata read failed

A successful exclusive filesystem link makes the complete report visible
BEFORE the following metadata read. If the read fails (for example a
transient I/O error querying the newly published name), the outcome is
receipt_commit_uncertain, never receipt_write_refused: an original report
may already exist and must not be overwritten or automatically retried.
The same rule applies to a later directory-sync or handle-close failure.
The published destination is checked independently from the staging file.

The real-filesystem regression
test_successful_link_then_metadata_error_is_commit_uncertain injects one
metadata I/O error AFTER the link has succeeded. It verifies the complete
JSON exists and that no extra stage remains BEFORE checking the uncertain
classification. Its RED source commit is
b893e02d18cbc5959127028221d26a4db76942f6.
