# Graph GN1 — real development-only Native startup

GN1 is **not Graph6 acceptance**. It addresses the first unexplained
`RESTRICTED_SOCKET` startup failure with one authentic daemon and provider
on a disposable Ubuntu GitHub Actions runner. Its source-only predecessor
`ci_graph_dev_fast.py` runs in seconds; GN1 is an opt-in physical startup.

## Execution boundary

`graph-native-startup-dev.yml` is manually dispatched only after a review
of the exact published Motionwright SHA and the pinned Semwright SDK
revision. An explicit `one_original_owner=allow-one-original` choice and
an RSA3072 independent reviewer public-key JSON are required. A new
private owner root must be created directly under `RUNNER_TEMP`; neither
the checkout, a previous R74 artifact nor any real user Store is reused.

The job compiles only pinned Semwright CLI/daemon/sandbox, Motionwright
provider, genuine shared-state actor and Manim runner, using the original
Cargo locks. It uses Semwright's first-party Linux driver sandbox profile
and has no trusted fallback or expanded grants. It creates a fresh
physical SQLite Store with the actual actor and a Core-issued project via
`project.create`, then uses Motionwright's original `graph_manifest` and
`graph_owner_config` source to start the restricted daemon. The final
single provider mount/handshake is also genuine. There is no fake Ready,
renderer effect or second attempt on a partially mutated Store.

Owner processes have bounded resources and a short socket deadline.
Unexpected exit, non-socket, deadline or incomplete owned-process-group
closure remain FAIL/UNKNOWN, with no automatic replay. This is a
development first-cause observation, not a reason to relax policy or
increase timeouts speculatively.

## Evidence and acceptance

Private configuration, original daemon streams, Store identity and
request/terminal receipts are collected under a 0700 fresh owner root
and sealed using the reviewed `ci_private_evidence` RSA-OAEP-SHA256 plus
AES-256-GCM envelope. Only the encrypted archive and an allowlisted
finite status/cause-stage JSON are uploaded. The private decryption key
does not enter the runner. An unsealed or unclosed effect never grants
success and must not be retried using the same identity.

GN1 success means only `PASS_DEV_NATIVE_STARTUP_ONLY`. It does not
establish an authenticated R74 producer/reviewer, Graph6 editing,
readback/reopen, six physical cases, final F/G2/G3, or release readiness.
Any failure requires reviewing the decrypted first original with its
exact source/SDK/run before assigning the causal fix to Graph or Native.

## Source-only check

Before enabling a physical GN1 run, the plan is inspected without
processes or credentials:

```sh
python -B tooling/ci_graph_dev_native_startup.py --mode plan \
  --repo . --sdk _semwright --expected-source <40-hex-commit> \
  --root /path/to/disposable-runner/gn1-<run-id>
```

Windows and Ubuntu regression tests for this selection never run Native:

```sh
python -B -m unittest tooling.tests.test_ci_graph_dev_native_startup -q
```

**Workflow registration:** GitHub's new manual workflow must be
available on the default branch before a dispatch is possible. Until
that happens, GN1 is source-reviewed but **NOT_RUN**. Do not force a
legacy Graph6 R74 workflow or borrow an old authenticated status as a
workaround.
