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

Undo and restore create new changes. They do not rewrite history or resurrect old grants.

## Renderer model

Semantic intent is versioned separately from renderer realization. A renderer declares supported vocabulary and losses. Swapping renderer rebuilds a realization and may produce explicit warnings/unsupported constraints rather than silently approximating every behavior.

## Graph and Effects

Motionwright stores its local dependency projection but canonical CURRENT/STALE/UNKNOWN evidence is admitted by Semwright Project Graph. Technical verification reports come from Semwright Effect Conformance. Creative critique is advisory and kept separate.

## Jobs

Long-running work is correlated locally but scheduled/executed through Semwright mechanisms. The UI may map canonical states into QUEUED/RUNNING/CANCEL_REQUESTED/SUCCEEDED/FAILED/CANCELLED/OUTCOME_UNKNOWN. Result applicability (for example a stale late render) is a separate dimension.
