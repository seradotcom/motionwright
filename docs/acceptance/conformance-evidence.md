# Conformance evidence and acceptance history

The public ledgers preserve 208 original requirement IDs and 60 original acceptance case IDs. The expansion retains its 64 requirement IDs and adds 64 acceptance cases. The technical evidence index binds all 396 records to their source-record and source-field digests and preserves 506 explicit requirement/case links.

This is traceability and recorded assessment, not a product acceptance certificate. A source field may contain several obligations. The 1652 preserved source-field occurrences, including 128 exact-text aliases, are not described as 1652 independently completed atomic criteria. Aliases preserve text identity; they do not transfer execution or acceptance.

## Separate assessment dimensions

Each row has an additive technical_assessment:

| Dimension | Meaning |
| --- | --- |
| implementation | Implemented, partial, absent or not assessed; referenced source presence is recorded separately. |
| integration | The exact integrated or branch snapshot being assessed. It does not imply completed behavior. |
| technical_verification | Result, actual execution kinds and scoped evidence IDs. |
| criteria | Every preserved field, its digest, observed evidence and unresolved assertions. |
| review | The method and kind of the review actually performed. Planned human work is not recorded as a completed review. |
| artifact_verification | Which logs or artifacts were admitted and what they do not demonstrate. |
| contract_acceptance | Open, externally blocked, accepted or legitimately not applicable, with pending fields and real human obligations. |

Legacy fields and their original baselines remain intact as historical observations. Current dimensional assessments do not rewrite those earlier observations. The 64 added cases start from their actual recorded state; schema migration does not execute a scenario.

## Evidence integrity and sufficiency

An evidence record identifies the executed source, product inputs, test or oracle definitions, observed assertions, result and retained output bytes. A passing record needs executed assertions with no failure or skipped count hidden in that claimed scope.

The execution profile must agree with its kind. Unit, integration, native and installed describe different operations. Native input decoding, a final master consumer, an external editor reopening and restoration in a second environment need their own observed surfaces. A test name or a native crate name cannot establish them.

Completing a criterion also requires a separate sufficiency review. It binds the exact canonical clause, its verification profile and the full digests of the evidence records. Every applicable obligation must be covered with an explained observation and the required execution surface. A record cannot award itself completion by adding a criterion ID.

These checks validate recorded identity, consistency and integrity. They do not authenticate a person or make semantic judgments true merely because metadata validates. Reviewers must inspect the actual assertions, execution context and outputs. Human review is required only for the applicable contractual obligations, and its completed report must bind the source and retained artifacts. Objective technical completion remains possible without inventing a human session.

## Source changes and historical results

Source and tracked JSON identities use explicit UTF-8 Git text with LF normalization, so a CRLF checkout does not silently change a source identity. Logs, archives, database snapshots and media use exact raw-byte digests; they are never normalized as source text.

Historical executions retain their original source SHA, run, job, attempt and outcomes. An input or test-definition mismatch makes the evidence stale for current complete claims. Its earlier result can remain recorded without becoming a new execution. A matching file name, green workflow or merge does not carry acceptance to a different source.

The assessment source is an explicit snapshot. Final release checks must additionally verify the candidate actually being considered. Successful validation of a blocked readiness report is not authorization to publish.

## Retained observations in this assessment

The initial source-scoped corpus includes selected SQLite replay/dispatch assertions, in-memory creative history assertions, synthetic PCM ducking assertions, intake identity guards and native H264 admission assertions. Source declarations are separate observations.

The retained archive contains selected execution-log observations and source-declaration observations. It does not contain final product masters, private acceptance media or completed installed/human acceptance sessions. Each record states those limits, and no complete criterion is awarded from this initial corpus.

## Validation

Run the ledger validators from the repository root:

    python tooling/acceptance_policy.py
    python tooling/creative_status_policy.py

The focused integrity regression suite is:

    python -m unittest tooling.tests.test_conformance_policy tooling.tests.test_acceptance_policy tooling.tests.test_creative_status_policy -v

The canonical public inputs are requirements-status.json and acceptance-status.json in this directory, the expansion requirements-status.json in docs/creative-production, and technical-evidence.json. Original private source text and internal review records are not published.
