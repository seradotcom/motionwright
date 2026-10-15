# Native command JSON admission — fail-closed boundary

This is a bounded reliability hardening for the **existing** Motionwright →
Semwright Native SDK acceptance harness. It does not introduce a new renderer,
Broker, recovery authority, release certificate or paid-provider path.

## Why

A subprocess exit code of zero is **not** by itself a verified Motion Canvas
render or AV master. Python's permissive `json.loads` normally accepts duplicate
keys with the last value winning and also accepts non-JSON numeric values
`NaN`, `Infinity`, and `-Infinity`. An extreme legal exponent such as
`1e999` can additionally become an infinite Python float. Such a document
must not be allowed to change an original native job's source outcome.

## Boundary

`tooling/native_av_failure_guard.py` now parses both success envelopes and
typed error envelopes with exactly one interpretation:

- Every JSON object at every depth rejects duplicate keys.
- Nonfinite numeric constants and numeric overflow are rejected.
- Success output is limited to 1 MiB before interpretation.
- The expected success structure remains an object.
- A malformed or oversized **zero-exit** response yields a sanitized
  `NativeCommandFailure`, fixed allowlisted reason, and
  `source_outcome=UNKNOWN`. No raw stdout, stderr, job reference,
  credentials, command arguments, or provider file paths leave the
  diagnostic.
- A malformed typed failure remains a failure and cannot attribute a
  provider generation or native terminal state from ambiguous JSON.
- Known-good receipts retain their existing behavior.

No caller may silently create a new effect grant or redispatch a mutating
render because this boundary rejected a receipt. The original job must be
reconciled through the same owner-bound Broker/Driver Host evidence.

## Evidence

The synthetic/adversarial tests cover root and nested duplicate keys,
nonfinite and overflowed numbers, syntactically invalid success,
oversized zero-exit responses, ambiguous typed failures, redaction, and
unchanged well-formed success. GitHub Actions runs both
`test_native_av_failure_guard.py` and
`test_native_cli_failure_redaction.py` through the existing
`v01-native-av-failure-redaction.yml` workflow.

Passing this test does **not** reproduce or fix provider-generation loss in
Motionwright #49 / Semwright #257, prove a real video render, or satisfy any
of the 60 independently NOT_RUN human acceptance cases.
