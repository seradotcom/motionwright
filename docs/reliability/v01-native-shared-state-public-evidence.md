# Motionwright v0.1 — safe public Native SDK shared-state evidence

**Scope:** CI delivery and failure diagnostics for the original owner-local
UI-service ↔ Native SDK ↔ Broker/Driver Host shared-state test. No provider
lifecycle change, Semwright source-pin change, grant bypass or v0.5 feature.

## Prior artifact exposure

The Native SDK Pin workflow uploaded the full directory
verification/native-sdk/github-actions/shared-state unconditionally.
A real successful disposable Broker test left calls.json (13 full native
invocations, including arguments, original opaque refs, session names and
response envelopes), semwrightd.log, a sandbox profile and a result
including private project identifiers. These files are not needed publicly
to demonstrate the ten original shared-state invariants.

The native workflow actually exercises:
- UI-seeded project observed by the native host;
- SDK write visible to canonical UI service and vice versa;
- stale/cross-project reference and forged actor rejection;
- two independently established native sessions observing the same base;
- concurrent CAS allowing exactly one winning update, one stale loser;
- post-CAS original state and final SDK write consistent in service reads.

## Closed publication

tooling/native_shared_state_public_artifact.py reads the original private
result.json and calls.json **inside the ephemeral CI runner** only on
a successfully completed job. It validates both SHA-40 source identities,
the pinned provider executable SHA-256 shape, exact ten named Boolean
invariants, final project revision and expected final title, and a typed
13-invocation native transcript: six observations, seven renames,
eight successful commands and five rejected commands, with consistent
exit status. It does not replay effects, query the Broker, retry a native
mutation or turn a failed run into verified native evidence.

Only a single public-evidence.json is uploaded, containing exact
Motionwright/Semwright source SHAs, ten known checked invariant names,
counts and SHA-256 digests of the *private* original result and calls.
It does **not** include original request bodies, response envelopes,
session files, owner/project/resource/generation IDs, process logs, raw
network/config settings or the original evidence files.

If the job failed, was cancelled or skipped, the publisher reads no
private source and emits only a NOT_VERIFIED public receipt. Malformed,
duplicated, mismatched or incomplete successful evidence fails closed
instead of publishing a false PASS. The upload path is restricted to the
new public-only directory even when GitHub Actions finishes with an error.

## CLI failure privacy

The same Native SDK shared-host test now refuses oversized/malformed,
non-object, contradictory exit/typed-outcome CLI envelopes with bounded
static errors. Both expected SDK rejections and genuine successes retain
their original typed data inside the protected runner. Unexpected native
CLI stdout/stderr, original argv/owner paths, untrusted provider provenance
and early daemon startup logs are never interpolated into GitHub Actions
exception strings. The original job is never silently restarted when a
native command result is uncertain.

## Evidence and limitations

The publisher was applied to a downloaded successful real GitHub Actions
Native SDK Pin artifact, observing all ten invariants and thirteen native
invocations while producing precisely one sanitized public JSON receipt.
Adversarial unit tests exercise source swaps, forged success claims,
contradictory native returns, missing/duplicated raw evidence, output
path overlap, failure semantics and private log/response suppression.

The native host's claims are still automated CI checks, **not** signed
owner-installed binary provenance, human visual review, guaranteed
provider-generation recovery, authenticated reviewer identity or approval
for a stable v0.1 release. Issues #49, #89, #91 and #126 remain open.
