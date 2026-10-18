# Conformance source-currency analysis

This read-only tool compares evidence and assessment records with exact committed Git source inputs. It helps distinguish historical observations from current-source verification.

Run the following commands from the repository root:

    python -B tooling/conformance_currency_impact.py --target HEAD
    python -B tooling/conformance_currency_impact.py --target HEAD --strict
    python -B tooling/conformance_currency_impact.py --target HEAD --json path/to/ignored/receipt.json

Use an exact 40-character lowercase commit SHA instead of HEAD when checking an immutable revision. An optional --portfolio argument can validate a private, ignored owner catalog against original clause hashes. Never commit the private catalog or its contents.

## Understanding the results

- **Current source candidate:** source revision, source lock and committed product/oracle inputs match. This does not establish product acceptance.
- **Historical source, same inputs:** referenced blobs appear unchanged, but the actual execution occurred on another revision.
- **Historical changed or unknown inputs:** source files changed or lack trustworthy identity.
- **PASS requiring reassessment:** an earlier technically sufficient field cannot be promoted to a new revision without fresh evidence.

The --strict mode exits with code 2 when the assessed source revision or lock differs from the target, or if currently recorded PASS criteria need reassessment. It does not alter evidence, demote ledger entries, invoke providers or modify source code. Store diagnostic receipts in a gitignored location.

## Verification boundaries

The original requirement identifiers and clause hashes remain normative. Evidence snapshots, input digests, test run identities and independent sufficiency reviews must be attached to the revision actually tested. Preserve historical evidence append-only. Whole-product approval requires installed-service, native-media, rights and any applicable human evaluations. A source-currency check by itself never establishes release readiness.
