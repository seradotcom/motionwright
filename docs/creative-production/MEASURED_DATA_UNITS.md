# MW05-E06-03 · Measured data with units (bounded implementation)

Status: **partial implementation, not measurement verification or complete v0.5 acceptance.**

## Contract, evidence and authorization

**Production → Measured data** attaches a declared numeric JSON/CSV table
to a content-addressed **existing Project Asset**. The asset must be imported
through Motionwright's normal asset path and expose a lowercase SHA-256.
The source asset ID, digest, versioned schema and numeric data are persisted
as ordinary project state. Missing assets, hash mismatches, unsupported MIME
types, duplicate keys and unknown source formats are rejected.

**This checks the imported Asset metadata identity only.** It does **not**
independently read, parse and compare the raw uploaded CSV/JSON bytes to every
declared measurement. That import is not evidence that its values were measured
correctly or that its author was authorized. The UI labels the rows as
**declared**, and requires independent source/rights/methodology review.
No verified-source or creative-approval claim is fabricated.

Tables use versioned schema motionwright.measured-table/1 with 1–64 rows.
Every row specifies a lower-case key, a bounded label, a signed integer
milli (thousandths), and one typed unit: count, percent, seconds,
milliseconds or bytes. Values are bounded to ±1,000,000 actual units.
No floating-point CSV parsing, LLM data cleaning, implicit unit conversion,
or automatic chart fitting occurs. Limits: 16 tables/project and 64
bindings/project. Extra keys are rejected by the Rust serde source schema.

Replacing a table requires a new asset digest and exactly the next dataset
version. Asset metadata remains immutable. Legacy format-2 projects missing
the new fields remain readable. Existing project-generation, revision CAS,
scene locks, native authorization and durable event journal remain in force.

## Explicit editable outputs

Bind a row to an **existing Canvas node**:

- **counter:** writes only a text node's text, with the source unit and
  explicit precision (0–3 decimal places).
- **label:** writes only a text node's text, with its source label, value,
  unit and authored precision.
- **bar:** changes only an existing rectangle's width. Minimum,
  maximum and maximum pixel width are all explicitly supplied. Out-of-range
  values are rejected; no zero origin, clamp, axis or chart claim is invented.

The object keeps its normal Canvas ID, style, position, layer, and native Film
semantics. A binding stores a computed baseline for that table and format.
Human edits to the **bound text or width** are rejected on the next source
update until the user detaches/rebinds; styling and independent geometry
remain editable. Detach retains the last authored Canvas object.

A single row drives one bounded graphic primitive, not a fabricated axis.
Live external feeds, full graph/chart construction, source-file ingestion,
time-series alignment, uncertainty labels and measurements made by an LLM
remain out of scope.

## Targeted invalidation

On table update the stored output is compared to the new derived output of
each matching binding. **Only changed projected outputs** rewrite their
respective node and mark that scene as draft. Unrelated scenes retain
their content and scene status. The persisted last_measured_impact records
changed datum keys, affected scene IDs and deliverable IDs. An empty
included_scene_ids list means a profile includes all scenes; an explicitly
scoped profile is marked only if one of its scenes actually changed. A
source-hash-only change with identical projected values affects no scenes.

This is a **dependency/invalidation notice**, not proof that every render
cache has been purged or that a new MP4 has already been produced. The
ordinary project revision still advances through one atomic CAS operation.

Missing bound data rows, stale source versions, property locks, human
changes to projected text/width, unit schema errors and unsigned versions
fail closed instead of fabricating replacements.

## Tests and native acceptance boundary

- Rust domain: tests in crates/domain/tests/measured_data.rs cover units,
  round-half-away-from-zero formatting, scoped scene/profile invalidation,
  missing rows, invalid axes, source digest mismatch, locks, detach and legacy
  serialization.
- Independent TypeScript mirror and tests: apps/studio/src/measuredData.ts
  and apps/studio/src/measuredData.test.ts.
- SQLite durable CAS and stale-revision check:
  crates/service/tests/measured_data.rs.
- Pinned Semwright Native Film projection of editable text/rectangle:
  crates/native/tests/measured_data.rs.
- Browser empty-state and no-invented-data guard:
  apps/studio/tests/measured-data.spec.ts.
- Three CAS operations in Semwright Native SDK:
  measured-table.upsert, measured-binding.upsert and
  measured-binding.detach.

All acceptance remains pending until CI passes the exact pushed revision.
No extra Core, scheduler, privileged execution route or spreadsheet backend
is introduced. Full verified ingest and real creative acceptance are still
open work.
