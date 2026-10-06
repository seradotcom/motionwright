# Architecture

## Boundary

Motionwright is an application, not another Semwright Core.

```text
React editor / Tauri commands / external client
                     |
                     v
              Motionwright service
                     |
          +----------+-----------+
          |                      |
          v                      v
  app-owned SQLite        Native SDK adapter
  model + CAS + journal          |
                                 v
                    Semwright Driver Host / Broker
                                 |
                +----------------+----------------+
                |                                 |
             Graph/Effects                  renderer drivers/jobs
```

Motionwright owns project identity, model validation, SQLite transactions, request receipts, branch/review semantics and local preferences. Semwright owns common authority and runtime boundaries.

## Transaction model

A project mutation carries:
- project ID;
- expected generation + opaque revision;
- request ID;
- exact request digest;
- bounded typed change.

The storage layer begins an IMMEDIATE SQLite transaction, loads the current resource, checks generation/revision, checks request receipt reuse, validates the resulting domain model, appends the change/event, updates the project document and stores the receipt atomically.

A preflight observation is useful for UX but never substitutes for the commit-time comparison.

## Revision model

Project resource version is `generation + revision`. Generation changes when a resource is intentionally recreated/imported as a new authority. Revision is monotonically increasing inside one generation and is serialized as a string at the Native SDK boundary so it remains exact across Rust/JavaScript.

Undo creates a new change rather than rewriting history. Portable restore preserves the source journal but rotates the resource generation before accepting any new write, so callers must observe a fresh base. Request receipts are execution-local and are deliberately excluded from portable backups; restoring an old package therefore cannot resurrect consumed deduplication keys.

## Storage evolution, paging and portable project state

SQLite uses `PRAGMA user_version` as an explicit storage-schema gate. A database whose schema version is newer than this binary supports is rejected before Motionwright creates or migrates application tables. Project documents also carry their own domain schema version and must validate before they enter the service boundary.

Project discovery has a summary projection with bounded keyset pagination over `(updated_at, id)`. Listing projects does not require deserializing every complete creative document.

The portable backup envelope is created from one `IMMEDIATE` SQLite transaction and contains the validated project document plus its complete committed change journal, format/schema metadata and a SHA-256 digest. Import supports a dry-run plan, verifies the digest and contiguous journal, rejects duplicate logical project IDs, preserves logical history, rotates `generation`, and does not import request receipts.

Asset bytes live outside SQLite in an immutable content-addressed store at `blobs/sha256/<prefix>/<digest>`. File ingestion streams through a private staging path, hashes while writing, syncs the staged bytes, and only then renames into the canonical digest path. Reusing a digest verifies the existing bytes rather than overwriting them. Reads are digest-verified and explicitly size-bounded. Registering asset metadata is a normal versioned `Change`; storage re-hashes the referenced blob before commit, so UI and Native SDK callers cannot publish a project reference to absent or mismatched bytes. Desktop local-file import composes blob admission followed by that same CAS-protected asset change. A failed CAS may leave an unreferenced immutable blob, never a broken project reference.

A self-contained project bundle is a directory with `manifest.json` plus the exact content-addressed blobs referenced by project assets. Export is staged into a new directory, copies and re-hashes every blob, writes the manifest last, then atomically renames the directory into its requested destination. Import has a dry-run that verifies a real non-symlink bundle root, a regular manifest, regular non-symlink blob files, canonical blob paths, unique digest membership, byte sizes, hashes, backup integrity and exact equality between the manifest blob set and project asset digests. Imported blobs are admitted before the project transaction; any interrupted import can therefore leave only unreferenced immutable blobs, never a project referencing missing bytes.

Local asset import applies the same regular-file boundary before content admission. SVG is sniffed in addition to declared type/extension: static SVG remains portable, while scripts, event handlers, externally-referencing hrefs, active embedded elements, CSS URL loading, XML entities and external stylesheets fail closed. The Tauri WebView has a self-script CSP with no remote frames/objects/forms and no privileged shell/filesystem/HTTP/process plugin surface. See `docs/security.md` for the exact claim boundary.

## Renderer model

Semantic intent is versioned separately from renderer realization. A renderer declares supported vocabulary and losses. Swapping renderer rebuilds a realization and may produce explicit warnings/unsupported constraints rather than silently approximating every behavior.

## Graph and Effects

Motionwright stores its local dependency projection but canonical CURRENT/STALE/UNKNOWN evidence is admitted by Semwright Project Graph. Technical verification reports come from Semwright Effect Conformance. Creative critique is advisory and kept separate.

## Jobs

Long-running work is correlated locally but scheduled/executed through Semwright mechanisms. The UI may map canonical states into QUEUED/RUNNING/CANCEL_REQUESTED/SUCCEEDED/FAILED/CANCELLED/OUTCOME_UNKNOWN. Result applicability (for example a stale late render) is a separate dimension.
## Native production connection

The renderer path is an explicit consumer of the Semwright Broker rather than an embedded scheduler. An owner-provisioned, digest-pinned CLI connection is revalidated on every call. The application allowlists production operations, checks Driver provenance and persists revision-bound receipts. A local receipt is historical application evidence only; Graph admission and Effect Conformance remain separate canonical authorities.

## Delivery boundary

Delivery profiles are application-owned versioned intent and travel with branch state. Caption sidecars are derived locally only from transcript segments whose timing evidence is known; an UNKNOWN alignment cannot be promoted to a timestamp. Final media production remains a Semwright-backed runtime concern, so configuring H.264, HEVC, ProRes, VP9, AV1, AAC, PCM or Opus never creates an execution or quality claim by itself.

## Public product handoff boundary

Motionwright can record versioned handoff bindings to Launchwright without mounting Launchwright
storage or importing its internal release domain. An input binding contains only a public resource
kind/ID, optional exact external revision and the Motionwright resource that consumed that context.
Artifact and evidence output bindings additionally require an exact SHA-256 digest. Context
bindings never smuggle artifact identity.

These bindings are local project records; creating one does not mutate the other product. A real
remote exchange still requires an authorized public service/client contract and must preserve the
same public IDs, revisions and artifact digests end to end. Until that service exists and is
exercised, the Integrations workspace labels the remote Platform path as an upstream gate rather
than presenting a mock as live.
