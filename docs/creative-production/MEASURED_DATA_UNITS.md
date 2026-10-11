# MW05-E06-03 · Measured data with units (bounded implementation)

Status: **partial implementation, not measurement verification or complete v0.5 acceptance.**

## Contract, evidence and authorization

**Production → Measured data** attaches a declared numeric JSON/CSV table
to a content-addressed **existing Project Asset**. The asset must be imported
through Motionwright's normal asset path and expose a lowercase SHA-256.
The source asset ID, digest, versioned schema and numeric data are persisted
as ordinary project state. Missing assets, hash mismatches, unsupported MIME
types, duplicate keys and unknown source formats are rejected.

**The SQLite CAS verifies original content-addressed bytes independently.**
Before accepting a table, Storage retrieves at most 64 KiB from the existing
immutable blob and checks its SHA-256; it parses either an exact JSON array of
typed rows or a deliberately narrow unquoted CSV with the header
key,label,milli,unit. The declared rows must equal the parsed source rows,
including order and units. An SDK request containing a fabricated metric fails
even if it references a valid imported digest. The Studio UI additionally
requires reselecting the matching source file, computes its SHA-256 locally,
and displays only read-only parsed rows.

This attests **byte identity and exact parsed values**, not the scientific
accuracy of the observations, measurement methodology, authorship, licensing
rights or independent quality certification. The UI consequently still
labels values as source-declared and requires human source/rights/methodology
review. No creative-approval or factual-truth claim is fabricated.

Tables use versioned schema motionwright.measured-table/1 with 1–64 rows.
Every row specifies a lower-case key, a bounded label, a signed integer
milli (thousandths), and one typed unit: count, percent, seconds,
milliseconds or bytes. Values are bounded to ±1,000,000 actual units.
The importer deliberately rejects quoted commas, locale-dependent decimals,
unbounded files and arbitrary CSV dialects. No floating-point CSV parsing,
LLM data cleaning, implicit unit conversion, or automatic chart fitting occurs. Limits: 16 tables/project and 64
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
Live external feeds, full graph/chart construction, general CSV dialects,
time-series alignment, uncertainty labels and measurements made by an LLM
remain out of scope. Bounded JSON-array/CSV-four-column byte ingestion is
implemented as a source-identity gate, not as a general ETL system.

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

The source-byte verification has a further negative SQLite CAS test proving
that a request whose values disagree with a genuine stored JSON file is
rejected without changing the durable project or revision. This is part of
the existing Storage transaction admission path, not a second data service.

## CI-only native measured graphic proof (not real-world measurements)

An isolated GitHub Actions workflow, measured-data-native-e2e.yml, uses
the exact pinned Semwright Broker, Motion Canvas and MLT render providers.
Its seed imports two genuine, immutable JSON source files containing explicitly
**SYNTHETIC numeric fixture values**. The first version projects a label and a
100-pixel bounded bar; the second verified source revision projects the label
as “Synthetic rate · 37.5%” and a 300-pixel bar, preserving object IDs.
A separate known decorative rectangle moves so the 180-frame H.264/AAC
decode can demonstrate actual non-static native video without pretending the
counter is an observed dynamic time series.

The sample is a real owner-local StudioService/CAS project with versioned
measured tables, two bindings and a portable measured.motionwright bundle.
The workflow verifies its source SHA-256 bytes, generation rotation, JSON
row version, exact source Canvas text and bar width, and independently decodes
the rendered MP4. Original Motion Canvas PNG regions are checked for
nonblank label, visible bar and bounded width. The resulting evidence includes
four sampled native frames, a contact sheet, decoded MP4 samples, SHA-256
identities and an unambiguous synthetic-source label. No OCR, chart-data
fabrication or external render path is substituted.

Five adversarial Python tests exercise the negative pixel gate using explicitly
synthetic images. Synthetic negative tests are **never** promoted to
positive render evidence. The pinned workflow output remains technically
**NOT VERIFIED** until the exact HEAD completes successfully. Even a green
workflow requires separate human creative approval and does not certify
scientific truth, licenses or rights.

All new acceptance remains pending until CI passes the exact pushed revision.
No extra Core, scheduler, privileged execution route or spreadsheet backend
is introduced. Broader externally audited measurement ingestion and real
creative acceptance are still open work.
