# MetricEvidence notice authority (bounded write invariant)

MetricEvidence is a semantic typographic study of a user-supplied figure, **not**
a verified data visualization or measured evidence. Its generated caption reads:

> METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED

The caption is an exact, six-role semantic source object. For an *attached*
MetricEvidence instance, every new project mutation checks that the stored
caption object exists and still equals the generated baseline: copy, paint,
geometry, motion keyframes, layering, opacity, parenting and all other visual
fields. Adding a property lock is allowed because a lock changes authority,
not pixels. Refusal happens through the same existing Motionwright service CAS
transaction; no native SDK or separate approval endpoint is introduced.

The browser editorial state mirrors the Rust write preflight. Neither the
browser nor a generated source note is an independent attestation of the
truthfulness of the underlying figure. Only actual source checks and human
review could establish that. This change does not add such checks.

## Historical projects and explicit authorship

Previously persisted projects are still **readable**; no on-disk data is
deleted, silently migrated or rewritten. Canonical Native SDK Motion Canvas
film preflight nevertheless refuses to render an attached study with a
tampered source notice. A historical attached component whose
caption was altered needs deliberate source restoration or an explicit
**Detach, keep objects** operation before any new revision. Detachment retains
the original canvas objects but removes the semantic-component integrity
contract. This is a deliberate authoring boundary, not a claim that free-form
canvas documents are verified. The product must not present detached studies
as independently sourced data.

The generated caption has authored entrance keyframes, so it is not visible
for all times during the entrance. This write guard protects the authored
animation contract, **not** every-frame visibility, independently measured
glyph bounds, or accessibility conformance. Those remain separate acceptance
requirements.

## Regression evidence

- Rust domain tests reject changed text and deletion but admit harmless
  property-lock changes and explicit detachment
- Studio browser-simulation tests preserve original project state on rejection
- SQLite service tests confirm that rejected edits cannot change durable
  project/revision, even after re-opening the database
- Canonical Native Film rejects historical imported tampering even before
  provider execution; source-pinned Native SDK real-render E2Es remain the
  authority for frame transport, source IDs and actual decoded media

All of these are engineering conformance checks; 60 independent human creative
acceptance scenarios remain **NOT_RUN**.
