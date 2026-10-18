# A5 — Source-currency impact without inherited PASS

This document describes a **read-only audit utility** for the canonical Motionwright v0.1 + v0.5 conformance portfolio. It is not a release gate by itself, an authorized merge, an installation test, or a substitute for the original private contracts.

## Scope

The tool `tooling/conformance_currency_impact.py` checks all retained public identities: **272 requirements**, **124 acceptance cases**, **1,652 original criterion hashes** and **506 normative links**. It reads source refs from the archived technical evidence index and compares their exact canonical Git text bytes against a chosen committed target SHA. It makes no modifications to product code, existing source ledgers, the private corpus or published artifacts.

Example:

```powershell
python -B tooling/conformance_currency_impact.py --target HEAD
python -B tooling/conformance_currency_impact.py --target HEAD --json knowledge/runtime/A5/source-impact.json
python -B tooling/conformance_currency_impact.py --target HEAD --strict
```

Use a complete 40-character lowercase commit SHA instead of HEAD to inspect an immutable snapshot. The optional `--portfolio` parameter accepts an **ignored private** A5 owner assignment file and checks the original text SHA for every clause before classifying owners. Do not publish that private file or use owner assignments without those hashes.

## Interpretation

- **Current execution candidate:** its recorded source SHA matches the target, its `SOURCE_LOCK.json` identity matches, all referenced product and oracle Git blobs match, and its execution is passing. This is not automatically a sufficient review.
- **Historical, same inputs:** the referenced blobs appear unchanged, but the execution occurred on another SHA. Even byte-identical inputs cannot relabel it as a new execution. A5 must evaluate source and environmental relevance independently.
- **Historical, changed or unknown inputs:** the referenced product or oracle source was modified, disappeared or had its source lock changed. Its prior result remains historical and requires a current-source execution.
- **Current but invalidated:** a record claims the target SHA, yet its source, oracle or lock no longer agrees. It cannot support PASS.
- **Sufficient current criterion:** the existing criterion is marked PASS and has a passing exact-source, complete evidence record and a distinct, current-source sufficiency review with matching retained hashes. Neither a previous sufficiency review nor a test name alone counts.
- **Pass requires reassessment:** the ledger records PASS, but the above current-source conditions are missing. Treat it as a review blocker until the owner supplies new proof.

The JSON report groups changed files by how many records and criteria they invalidate, includes links to the affected canonical cases/requirements, and separates implementation, integration, verification and contractual acceptance states. The optional owner report never reproduces original private clause text.

A `--strict` invocation exits with **code 2** when the ledger's assessed SHA/lock differs from the target or when it advertises a criterion PASS that needs reassessment. This flag **does not** demote criteria, revise evidence, or claim a release. When A6 adopts it into impact-selected CI, it must use the exact checked-out SHA and the correct A5-owned ledger branch; do not silence the error by changing the target argument.

## Ownership and closure protocol

1. A1 identifies the exact integrated source/head and reviews the A5 impact report. A5 alone changes the source-bound conformance ledgers. A2 owns persistence/runtime, A3 Studio, A4 native production, and A6 CI/test execution.
2. For changed source inputs, the product owner provides narrowly selected, **new** assertions and original output artifacts on the exact SHA. Preserve older failed and successful recordings. Do not rerun a completed or UNKNOWN native side effect.
3. A5 checks canonical criterion hashes, product+oracle blob digests, original CI job/run, retained media, execution limitations and the exact sufficient obligations. Add the new evidence and separate review **append-only**; a renamed requirement keeps its history.
4. A6 independently runs the appropriate impact tests on the candidate head. A1 alone merges. Verify source currency again if main advances during review.
5. **No complete scenario or release can be inferred** from a source pass, compiler success, synthetic proxy, CI workflow summary, unrelated historical result or an unverified human signature. Installed workflows, original audio/source rights, real native frame clocks, acceptance cases and any required human review remain separately gated.

The tool outputs a diagnostic snapshot rather than a promise of product completeness. Its purpose is to prevent accidental acceptance inheritance as Motionwright's integrated product changes.
