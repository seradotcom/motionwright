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

The current portable backup envelope is created from one `IMMEDIATE` SQLite transaction and contains the validated project document plus its complete committed change journal, format/schema metadata and a SHA-256 digest. Import supports a dry-run plan, verifies the digest and contiguous journal, rejects duplicate logical project IDs, preserves logical history, rotates `generation`, and does not import request receipts.

This envelope is the metadata/journal foundation, **not yet the final self-contained media package**. Asset bytes still need the content-addressed blob store and portable manifest before full cross-machine backup acceptance can be claimed.

## Renderer model

Semantic intent is versioned separately from renderer realization. A renderer declares supported vocabulary and losses. Swapping renderer rebuilds a realization and may produce explicit warnings/unsupported constraints rather than silently approximating every behavior.

## Graph and Effects

Motionwright stores its local dependency projection but canonical CURRENT/STALE/UNKNOWN evidence is admitted by Semwright Project Graph. Technical verification reports come from Semwright Effect Conformance. Creative critique is advisory and kept separate.

## Jobs

Long-running work is correlated locally but scheduled/executed through Semwright mechanisms. The UI may map canonical states into QUEUED/RUNNING/CANCEL_REQUESTED/SUCCEEDED/FAILED/CANCELLED/OUTCOME_UNKNOWN. Result applicability (for example a stale late render) is a separate dimension.
