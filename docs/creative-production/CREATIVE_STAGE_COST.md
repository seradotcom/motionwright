# MW05-E03-04 — bounded creative-stage cost evidence

This work extends the existing Performance Acceptance Evidence workflow.
The existing service acceptance benchmark already executes **nine synthetic
projects**: three unrelated predefined briefs by three sizes (8, 32 and
96 scenes). Each project commits exactly ten source edits through the
regular StudioService/SQLite transaction and reopens the store. The
benchmark saves per-operation measured microseconds and separate cache
lookup and validation measurements.

## New stage cost receipt

The exact-source consumer tooling/creative_stage_cost.py reads the original
acceptance_bench report, validates its complete nine-project matrix and all
ten ordered operations per project, and writes a new receipt named
verification/performance/creative-stage-cost.json. No extra renderer,
scheduler, service or benchmark backend is created.

The new editorial grouping deliberately retains the existing operation
identity and recorded microsecond values:

- concept_and_intent: rename, narrative premise, first scene objective;
- shot_timing_and_review: scene status, ripple duration, renderer selection,
  and global marker insertion;
- source_safety_and_revision: project lock, unlock, and final objective.

All three group timings reflect ONLY the ten existing local SQLite CAS
commit operations, not a stage-level video render. Stage totals and
per-dataset values are derived directly from real measured per-operation
microseconds; the reported overall revision-ten elapsed microseconds
includes any other benchmark overhead. There is no measurement of the
human time to draft a creative brief or review a storyboard.

The report separately preserves the existing cache evidence: store cost,
validated lookup cost, actual validated CAS blob bytes, and number of
preview-index regeneration units avoided. A skipped preview-index work
unit is **not** a saved video frame, measured render time improvement or
cash saving. Those values are deliberately null/NOT_MEASURED.

## Running it

GitHub Actions uses the exact temporary source checkout and executes:

    python3 tooling/creative_stage_cost.py \
      --input verification/performance/report.json \
      --source-sha "$(git rev-parse HEAD)" \
      --output verification/performance/creative-stage-cost.json

The CLI refuses an incorrect source SHA, unrecognized generator,
reordered/duplicated operations, missing datasets, inconsistent revision
counts, noninteger or negative durations, mismatched cache digests,
invalid cache statistics, invented user feedback or other benchmark
claims. It creates the output once without overwriting an existing
receipt. Errors use stable codes, not raw private source diagnostics.
The original report is not changed by the consumer.

The performance workflow retains its original resource-usage and native
CAS constraints and uploads the new file alongside existing evidence.
A green run demonstrates **these nine measured synthetic source edits
and cache checks**, not a faster renderer, fully incremental media
pipeline, fair competitor advantage, human creative quality, or v0.5
acceptance.

## Remaining work

For full MW05-E03-04 scope, measure independently a cold vs warm native
provider Film, actual changed-frame regeneration, audio-mix rebuilding,
the cost of validating each rendered frame and final media master,
and an equivalent no-cache control using identical source and hardware.
Measure confidence/reproducibility over repeat runs, report errors and
outliers, and separately gather real editors' creative revision costs.

The requirement remains partial; the 60 private product acceptance cases
stay NOT_RUN. The Native SDK source pin is unchanged.
