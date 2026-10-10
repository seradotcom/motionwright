# Motionwright v0.5 — competent direct-renderer comparison

**Requirement:** `MW05-E09-04 — Benchmark competente` in the v0.5
`SRS_DELTA_V05.md`: compare the same briefs, sources, budget and revisions
against capable tools and a baseline without Semwright; publish task-by-task
results, limits and unfavorable results; do not use an intentionally poor prompt
or a competing renderer deprived of equivalent features.

**Implementation scope:** paired evidence validator and human-operator protocol.
This is **NOT** a completed commercial, usability or design benchmark. No one
has recorded a ten-revision direct-HyperFrames comparative run in this CI
acceptance lane. The canonical Semwright Broker path must pass independently.

## Reproducible operator workflow

Run `tooling/creative-benchmark/make_protocol.py` with
`--output-dir <new-directory>`. It produces a reproducible **NOT_RUN**
synthetic sample, ten explicitly authored edit requests, exact SHA-256 inputs
and a schema-valid `study.json`.

For a *real* design comparison, an authorized owner must replace the synthetic
brief with a real brief, declare and digest-bind the actual product/media
source assets, set `mode="real_work"`, preserve a budget shared across arms,
and ensure both systems support the **same** required feature set. The
operator of the direct renderer must be competent with the current exact
renderer version and must provide source-backed evidence of preparation.
Do not ask one operator to use richer native features while disabling them
for the competitor.

For each system record an *editable native source project* and a rendered
artifact at baseline (revision 0) and after each of the *same ten* requested
human changes (revisions 1–10). Attach exact SHA-256 to the input instructions,
source project and rendered file. Preserve elapsed time, paid tool cost,
rights boundaries, human-locked edits, technical problems and all negative
observations; over-budget attempts remain in the result.

Both arms must be Motionwright **through the canonical Semwright Host**
versus either competent direct HyperFrames or direct Blender. A direct
HyperFrames runner embedded inside Motionwright is **not** a substitute for
the canonical Host arm. A screenshot or flattened MP4 is **not** an editable
native source. A test of synthetic fixtures is not a real campaign benchmark.

Use two or more independent human reviewers with blinded arm ordering and
score each system on the same eight categories (1–5): narrative, composition,
typography, motion, editability, truthfulness, responsiveness and audio.
Record rejected ideas and differences in access, hardware, time, cost, and
operator fluency in the review limitations. Do not hide a result if direct
HyperFrames performs better.

Then execute:

```bash
python3 tooling/creative-benchmark/benchmark_compare.py \
  /path/to/study.json \
  --root /path/to/authorized-evidence \
  --output /path/to/NEW-comparison-report.json
```

The script never installs software or executes a source project. It validates
artifact locality, exact bytes, shared revision intent, operator/capability
parity, budget, cost, source editability, human locks and blinded review
evidence. It records comparable totals and **does not select a winner**,
declare creative quality based on hashes, grant execution permission or
approve publication.

## Evaluation state machine

| Condition | Status | What it establishes |
|---|---|---|
| Generated protocol, no arms | `NOT_RUN` | Correct study structure; zero result |
| Both actual arms, sources and renders, no blind reviewers | `EVIDENCE_NEEDS_BLIND_REVIEW` | Artifacts and operator measurements present, human quality unknown |
| Complete synthetic test | `SYNTHETIC_PROTOCOL_ONLY` | Validator works, no real product comparison |
| Both real arms and blind reviewers, no owner decision | `AWAITING_OWNER_DECISION` | Assessed task, independent publication gate remains |
| Owner records approval for complete real comparison | `OWNER_DECISION_RECORDED` | Audit trail, **not a release or winner assertion** |

An invalid or mismatched source, unauthorized/unavailable file,
inconsistent revision request, stale source, missing competitor capability,
missing editability or forged quality evidence is rejected instead of
producing a misleading partial "PASS".

## Security and reproducibility

Everything uses the existing project and artifact paths. The validator
checks relative paths and rejects symlinks, unknown file formats, and any
file over a defined bounded inspection budget. There is no arbitrary
HTML/Python execution and no network use. The demo CI generates only a
`NOT_RUN` protocol ZIP/artifact, never a fabricated comparison.

**Current acceptance:** protocol and negative-path Python tests can be
automatically checked, while actual execution through the canonical
Semwright Broker, operator expertise, performance metrics, genuine blinded
quality ratings and owner release approval all remain to be obtained.
