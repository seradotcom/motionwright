# Project row authority

A stored Project is admitted together with the SQLite row that contains it.
The full document must validate, and its ID, non-nil generation, revision and
exact title must agree with the row columns. A valid document copied into another
project's row is rejected; its identity cannot redirect a requested operation.

## Consumers and transaction boundary

The storage crate uses the same private row decoder for `load_project`,
`list_projects`, `apply`, `inspect_file_import`,
`append_production_receipt` and `claim_recovery_stage`.
The read of all authority columns and the document is one SQL statement.

Mutation and production methods perform that read after acquiring their existing
`BEGIN IMMEDIATE` transaction and before accepting a stored result, comparing the
caller's expected stamp, reading source/CAS bytes or writing a journal/receipt.
File-import inspection retains its existing deferred read snapshot.
Full-document reads reject inconsistent rows without repairing them.

The separate portability export and durable project-creation validators already
check their row bindings. This change preserves those validators and their error
contracts. Column-only paginated summaries continue to avoid loading documents;
a successful summary is not proof that its Project JSON has been validated.

## Historical results

The current row must be coherent before an old result can be returned. That
check does not require the historical result's title or revision to equal the
latest Project. A verified original file import remains observable after later
edits and removal of its original file or CAS object. Returning that result does
not reimport a source or assert that the source is still available.

Production observations retain their existing rule permitting a revision at or
before the current Project revision. A new recovery claim still requires the
exact current revision and the existing generation/request/recovery policy.
This check does not authorize retries, replace an UNKNOWN outcome, or infer a
new effect from an absent receipt.

## Rejections

Row/document mismatches use the existing domain-invalid storage error.
File-import inspection maps invalid current JSON, document or authority columns
to its existing invalid-file-import-receipt error. Valid rows with a foreign
expected resource/generation or stale revision retain the existing conflict
classifications. No storage schema, migration or public DTO is added.

## Regression coverage

The `project_row_authority` integration target preserves the original ordinary
commit/replay/reopen and cross-project substitution tests. Additional cases
corrupt each authority column, check full-document reads, reject corrupt rows
before replay or a missing CAS lookup, and compare both projects' raw SQL
documents, journal, receipts and disposable cache registrations.

The tests also cover historical import inspection after later edits and source
deletion, exact titles, unchanged column-only summaries, and the distinction
between historical production observations and current recovery claims.
Independent SQL fixture rows are not evidence of rendering or native cache
admission.

Source review or unit-test success does not establish crash durability,
power-loss recovery, a real Host/provider execution, cross-machine restore,
actor/grant authorization or human acceptance. Those claims require their own
source-bound scenarios and evidence.
