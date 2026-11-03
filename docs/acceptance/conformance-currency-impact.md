# Conformance evidence source-currency impact

The source-currency checker compares canonical Motionwright requirements and
their attached technical evidence with an exact Git commit. It is **read-only**:
it cannot edit requirements, award PASS, assert human approval or authorize
publication.

It examines **272 requirements**, **124 acceptance cases**, **1,652 canonical
criterion hashes** and **506 normative links**. Source and test definitions
are compared with committed Git source/test text under the recorded
`git-text-lf` digest convention and with the recorded source lock. A matching
historical result is not proof of a new execution on a later checkout.

## Development changes

For an incremental review during development, use the read-only source planner:

```sh
python -B tooling/conformance_development_impact.py --repo . --target HEAD
python -B tooling/conformance_development_impact.py --repo . --baseline <full-ancestor-sha> --target <full-target-sha>
```

Without `--baseline`, the comparison starts at the historical assessment.
An explicit baseline must identify an authenticated ancestor of the target.
The planner reads the existing catalog and compares committed Git objects;
it does not reopen historical media, regenerate ledgers, renew evidence, or
run tests. Dirty or untracked working-tree changes remain outside that
comparison and require the owner's attention.

The report lists direct source/oracle associations and unmapped changes.
Its `impact.phase1_required_impact` subsection narrows the existing
catalog to the original **247 required Phase 1 CR-/ACC- rows and 872 atomic
criteria**, without reconstructing the 396-row/1,652-field full contract.
It distinguishes historical record associations from affected Phase 1 rows
and fields. Changes to global SDK/dependency pins may affect many historical
records, including later-phase families; those counts never prescribe a
whole-product rerun or automatically grant a new PASS.

`dependency_or_environment_paths_changed` also identifies nested manifests,
locks, toolchains, Cargo configuration, Python requirements and constraints,
and CI definitions: even a mapped dependency file (such as the pinned Native
custody requirements) can affect other consumers or the execution environment.
Select the affected G1 and physical G2 cases from that scope. A review flag
does not require a new independent review for every trivial edit or a full
test run; the owner can record a bounded no-impact conclusion where justified.

Retain the original identity of reusable evidence. Unchanged inputs may
support a justified equivalence but never establish a new execution on the
target. Final acceptance still requires the strict checks below on the
nominated frozen source, including original artifacts and required reviews.

## Strict source and acceptance checks

From the repository root:

```sh
python -B tooling/conformance_currency_impact.py --target HEAD
python -B tooling/conformance_currency_impact.py --target HEAD --json source-impact.json
python -B tooling/conformance_currency_impact.py --target HEAD --strict
```

Use a full lowercase 40-character commit SHA instead of `HEAD` to inspect an
immutable snapshot. The report destination is caller-selected; do not
unintentionally commit generated evidence.

The `--strict` command exits **2** if the declared assessment revision or
source lock is different, or if any formerly passing criterion lacks a
sufficient current-source execution and review. That outcome is deliberate:
a structurally consistent ledger can still contain outdated evidence. Do not
silence strict errors by targeting an older checkout.

## Reading results

- **Current execution candidate:** its recorded source, test definitions and
  source lock match the target. A separate sufficient review is still required.
- **Historical, same input bytes:** source/test blobs match, but the test
  originally ran at another revision. It remains historical.
- **Historical, changed or unknown:** any source or test differs, disappears,
  resolves to a symlink or cannot be verified as strict UTF-8 text.
  Current-source evidence is needed.
- **Non-text reference:** a blob that fails strict UTF-8 decoding is reported
  with a "non_text" status and a null target_sha256. Other references and
  records are still classified. The affected record cannot become a current
  execution candidate or a historical same-input match. This does not supply
  a binary digest contract: raw hashing and replacement decoding are never
  substituted for the recorded git-text-lf identity.
- **Sufficient current criterion:** the existing PASS has an exact-source
  complete execution and a separate matching sufficiency review.
- **PASS requiring reassessment:** the existing PASS lacks one or more of
  those bindings. This is a review blocker, not proof that product behavior
  has regressed.

The JSON output includes changed-file hotspots, canonical affected criteria,
their linked acceptance scenarios, and separate implementation, integration,
technical and contract acceptance states. It does not infer internal teams,
ownership assignments or human signatories.

## Recording new evidence

Review the changed source and oracle first. Collect the smallest meaningful
new test selection on the exact checkout. Preserve old successful and failed
receipts as history. Capture source hashes, original runs, artifacts and the
criterion's explicit obligations before writing a new sufficiency review.

Installed workflows, native media, source rights, accessibility and any
required human evaluation remain separate acceptance obligations. A successful
source-currency check is **not** a product release authorization.
