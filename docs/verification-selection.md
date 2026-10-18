# Source-bound verification selection

`tooling/ci_impact_plan.py` records a conservative test recommendation for an
exact Git source. Its initial deployment is **shadow only**. It never schedules,
skips, cancels or retries a production job, modifies branch protection, changes
an existing workflow, or declares a release ready.

The lightweight `Verification Plan (shadow)` workflow adds executable guardrail
tests and retains the plan. All existing build, native, security, installation
and acceptance jobs continue to run under their existing triggers.

## Four verification stages

| Stage | Purpose | Current selection behavior |
| --- | --- | --- |
| G0 | Source policy, format/lint, basic security, contracts and fast negative controls on every PR and main update | Existing CI remains mandatory in the plan. The new lightweight workflow exercises the selector and evidence/conformance validators. Existing Rust format/lint jobs remain unchanged. |
| G1 | Unit and integration coverage for affected modules and their consumers | The planner records the union of affected Cargo packages and their reverse path dependencies, including dev/build, optional, target-specific and workspace-inherited paths. Known verification helper changes select their actual workflow consumers. |
| G2 | Real media, persistence/recovery, installers, and platform coverage when the impact requires them | Shared product Rust and Studio changes conservatively recommend the full observed workflow inventory. Recognized isolated verification helpers use closed risk mappings plus direct references from current and previous workflows. Unknown paths or incomplete dependency data expand the recommendation. |
| G3 | Complete candidate verification | `--candidate` recommends every workflow present in the exact checkout. Scheduling the applicable full job/matrix inventory and admitting its original evidence remain separate work. |

The present G2 mapping intentionally does not claim function-level isolation in
the large shared product crates or between Studio and native producers. Smaller
selections require reviewed contracts and evidence that all dependent behavior
is covered. A filename suggesting “Manim” is not sufficient to exclude storage,
shared compilation, desktop wiring or native source consumers.

The planner operates at workflow granularity; it does not launch a duplicate
matrix. A recommendation can contain an existing workflow that combines cheap
format/lint checks with expensive tests. That grouping is retained until a
separate, reviewed refactor preserves the same required checks.

## Source identity and change range

Planning requires Python 3.11 or later, a full local Git object database, an
exact 40-character lowercase base/head/checkout identity, and a clean source
checkout without modified, untracked or ignored input files. Keep receipts,
caches and generated outputs outside the source checkout. No dependency
downloads or builds occur during planning.

For a pull request, the tool computes the unique merge base of the complete
base and head history. It includes the entire merge-base-to-head change range,
so earlier commits in a stacked branch are not lost when the last commit only
changes a README. When testing the synthetic PR merge, it also includes the
base-to-merge delta and requires the exact ordered parents `[base, head]`.

The tool can also describe an exact PR head checkout. Such a plan is labeled
`source-head`, and it does not establish that the integrated PR merge works.
The supplied workflow checks out the event source and therefore normally records
the synthetic merge for pull-request events.

Both names of a rename/copy and the original paths of deletions remain in the
change inventory. Cargo relationships are collected from old and current
source trees so removal of a dependency edge does not erase its original
consumer. Git blobs are read directly: no source symlink is followed, no Git
replace ref is accepted, no external diff/text conversion runs, and no `cargo
metadata` command can fetch or execute code.

The plan includes base/head/checkout SHAs, tree identities, PR merge base,
checkout parents, manifest hashes, workflow definition hashes, changed paths,
selection reasons, the actual selector hash and a canonical plan digest.
Missing or malformed Cargo information expands selection. Unsafe paths,
shallow history, invalid revisions, a dirty checkout or an unrelated merge
checkout reject planning. Removed workflows carry unresolved dispositions;
they cannot disappear silently from successful aggregation.

Example with the full source identities recorded from the PR event:

```text
python tooling/ci_impact_plan.py plan --repo . \
  --repository seradotcom/motionwright --event pull_request \
  --base BASE_SHA --head HEAD_SHA --checkout CHECKOUT_SHA \
  --output PRIVATE_OUTPUT/impact-plan.json
```

Replace the uppercase placeholders with the exact values. A manual workflow
dispatch without a comparison base produces a full candidate recommendation;
it never treats an empty or missing base as proof that no tests are needed.
A new branch push with no previous commit and a non-ancestor push comparison
also expand to the full inventory.

## Executable G0 guardrails

Two lightweight jobs invoke `tooling/ci_test_runner.py` in both discovery and
dotted module modes, in fresh subprocesses, with the same exact event checkout
and `--require-complete`. They retain separate original stdout/stderr, exit
codes, counts, source/tree/test/tool hashes and source postchecks. Artifact
names distinguish the jobs and include the checkout SHA, run ID and attempt.

For a failed unittest run, `passed` remains `null`: footer failure/error counts
can count several subtests within one test case, so subtracting them from
`tests_run` would invent a case-level pass count. The original footer counters
and logs remain available, and a failed run still fails the gate.

The suites have explicit platform assignments:

| Suite | Required runner |
| --- | --- |
| `test_conformance_policy.py` | `ubuntu-24.04` |
| `test_acceptance_policy.py` | `ubuntu-24.04` |
| `test_acceptance_session.py` | `ubuntu-24.04` |
| `test_acceptance_evidence_identity.py` | `ubuntu-24.04` |
| `test_acceptance_cross_checkout.py` | `ubuntu-24.04` |
| `test_ci_impact_plan.py` | `ubuntu-24.04` |
| `test_acceptance_manifest_reparse.py` | `windows-2025` |

The complete manifest suite runs on Windows: all 13 current tests remain
required in each entrypoint, including the two real NTFS junction controls.
The suite is not filtered to individual tests, and platform skips remain a
failure under `--require-complete`. Its `TMPDIR`, `TEMP` and `TMP` point to
`RUNNER_TEMP`, outside the source checkout, so the private-evidence fixtures
can enforce their existing storage boundaries. Both jobs keep their reports
outside the source checkout and upload them even after failure.

The Linux job also retains the shadow plan. The final `G0 verification
complete` job requires successful results from both jobs; a missing, failed,
skipped or cancelled job cannot produce a complete G0 result. This aggregation
performs no additional product tests and does not dispatch a test matrix.

`conformance_policy.py` is an importable validator without a command-line
entrypoint. Running `python tooling/conformance_policy.py` alone executes
definitions and can exit zero without validating anything. Its actual tests
and callers are the verification entrypoints.

Positive and negative fixtures exercise missing/swapped/stale source evidence,
skipped or empty checks, source identity, immutable history and automated
acceptance boundaries. They test validators. They do not prove physical audio,
decoded frames, an installed application, native recovery, or human review.
Each job fails on an omitted or failed required test; it does not turn an
environment limitation into an unqualified PASS.

## Fail-closed receipt consistency

The optional `aggregate` subcommand compares a plan, a **separately reviewed
expectation manifest**, and retained observations:

```text
python tooling/ci_impact_plan.py aggregate \
  --plan PRIVATE_OUTPUT/impact-plan.json \
  --expected PRIVATE_OUTPUT/expected-runs.json \
  --observed PRIVATE_OUTPUT/observed-runs.json \
  --output PRIVATE_OUTPUT/receipt-consistency.json
```

This is a deterministic consistency check, not another conformance ledger.
It is not connected to a required GitHub check during the shadow phase. The
caller must independently authenticate the original GitHub run/job records,
source receipts and retained artifact bytes, and establish the complete
applicable job/check inventory. This tool cannot establish authenticity from
JSON fields or a supplied digest alone. It returns
`RECEIPT_CONSISTENCY_ACCEPTED`, never a product PASS or a release authorization.

Both input envelopes require `schema_version: 1`, the exact `plan_id`, the
repository, and a `runs` list. Each expected run contains:

| Field | Required meaning |
| --- | --- |
| `workflow` | Selected repository-relative workflow path |
| `workflow_sha256` | Exact workflow definition digest from the plan |
| `run_id` | Original GitHub run ID, encoded as a positive decimal string |
| `run_attempt` | Exact current expected attempt, encoded as a positive integer |
| `event_sha` | Original run event SHA; distinct from a dispatched source when appropriate |
| `workflow_sha` | Original workflow source commit from the execution receipt |
| `jobs` | Complete applicable matrix job names, each with a nonempty `checks` list of exact required check IDs |

Observed runs preserve those execution identities plus `status`, `conclusion`
and their jobs. Every observed job requires the exact expected name, original
positive-decimal `job_id`, `run_id`, `run_attempt`, a completed success,
`original_log_sha256`, and a `source` object containing the exact plan ID,
checkout SHA and tree SHA. Its `checks` list contains `{id, outcome}` records;
every expected ID must appear once with `outcome: "passed"`.

All selected workflows and declared applicable matrix jobs must be present.
Missing, extra, duplicated, skipped, cancelled, failed or incomplete selected
records reject aggregation. A successful workflow exit cannot compensate for
zero checks or a skipped required check. Reuse of a run/job identity, a stale
attempt, another event/workflow definition, or a different checkout rejects
aggregation. **An old commit with an identical tree still does not become a
test execution of the new commit.**

The expectation manifest is a trust input, not something to infer from only
the successful jobs in a run. An incomplete manifest cannot establish full
matrix coverage. Existing workflows with conditional or optional jobs need a
reviewed applicability inventory; the tool does not silently guess exclusions.
No adapter is included here to certify existing GitHub artifacts. Their
admission and any justified reuse remain under the existing evidence workflow.

## Efficiency accounting and remaining work

Shadow planning records **executions avoided: 0** and **minutes saved: 0**.
These are actual effects of the current implementation. A long recommendation
does not launch those workflows, and a shorter recommendation does not cancel
the workflows already selected by current triggers.

Before any selection can control job execution, the integration review must
compare shadow recommendations with actual run coverage, review uncovered
dependencies, retain exact applicable matrix inventories, and verify the
fail-closed aggregation against authentic receipts. Required checks must remain
present on every applicable PR. Unknowns must broaden or block the decision.

The full candidate also needs the technical obligations that existing
workflows do not yet implement. “Every observed workflow” is not equivalent to
“every original requirement verified.” Original source-bound evidence,
installation/update/interruption/recovery, real decoded media and any required
human observations remain separate release obligations. A candidate plan and
green validator tests cannot close them.
