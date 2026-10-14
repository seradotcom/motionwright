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
