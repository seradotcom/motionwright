# Independent product acceptance: private evidence intake

Motionwright tracks 208 implementation requirements and 60 product acceptance cases. Source tests, OS package jobs and Semwright native-render CI are **not product acceptance**. The 60 cases remain `NOT_RUN` until a separate evaluator has actually executed the corresponding **private specification scenario** on the correct application build and reviewed its result.

The canonical private product specification is **not published in this repository**. The public repository contains only `ACC-001` through `ACC-060` identifiers, status values, and links to implementation evidence. Do not reconstruct case descriptions from code tests or guess a human acceptance verdict.

## Prepare a private session

From the exact Motionwright source checkout that produced the installed test build, create an evidence directory **outside this public repo**. Do not run expensive native render tests on a space-constrained developer workstation; use authorized installed-device test environments and CI runners as appropriate.

```bash
mkdir -p "$HOME/Downloads/motionwright-acceptance-private/evidence"
python3 tooling/acceptance_session.py init \
  --source-sha "$(git rev-parse HEAD)" \
  --output "$HOME/Downloads/motionwright-acceptance-private/session.json" \
  --executed-by "operator-identifier"
python3 tooling/acceptance_session.py verify \
  --manifest "$HOME/Downloads/motionwright-acceptance-private/session.json" \
  --evidence-root "$HOME/Downloads/motionwright-acceptance-private/evidence"
```

The new session contains precisely 60 cases, all `NOT_RUN`, and the exact Motionwright and Semwright Git SHAs; it never creates `PASS` automatically. The session JSON is created with no overwrite, so a prior assessment cannot be silently replaced.

An authorized evaluator must use the original private acceptance specification to execute each case on the installed product. Reviewers should retain traceable **actual evidence**: unmodified recordings, screenshots, readback files, full native job receipts, hashes, crash logs or CI artifacts as appropriate to the particular private case. For example, after explicitly producing `evidence/ACC-001/screen.png`:

```bash
python3 tooling/acceptance_session.py fingerprint \
  --evidence-root "$HOME/Downloads/motionwright-acceptance-private/evidence" \
  --path "ACC-001/screen.png"
```

Copy the returned `path`, `sha256` and `size_bytes` descriptor to the matching case in `session.json`, alongside the real execution time, operator, reviewer and observations. Set `PASS` **only** after a distinct independent reviewer has actually checked the primary evidence against the corresponding private scenario.

A valid manually reviewed `PASS` record has the shape:

```json
{
  "id": "ACC-001",
  "status": "PASS",
  "executed_by": "operator-identifier",
  "reviewed_by": "independent-reviewer-identifier",
  "independent": true,
  "executed_at_utc": "2026-10-08T20:00:00+00:00",
  "reviewed_at_utc": "2026-10-08T21:00:00+00:00",
  "notes": "Specific observations and reasons the private scenario passed",
  "evidence": [{
    "path": "ACC-001/screen.png",
    "sha256": "replace-with-real-64-lowercase-hex-sha256",
    "size_bytes": 12345
  }]
}
```

This is a **schema example**, not actual evidence or an assertion that `ACC-001` passed. A `FAIL` requires actual evidence and an execution identity/time; a `BLOCKED` requires execution identity/time and a written reason. `NOT_RUN` cannot contain a positive review or media evidence. Output `source.motionwright_sha` must identify the tested build, not whichever newer main happens to exist when reviewing.

## Verify completeness and integrity

```bash
python3 tooling/acceptance_session.py verify \
  --manifest "$HOME/Downloads/motionwright-acceptance-private/session.json" \
  --evidence-root "$HOME/Downloads/motionwright-acceptance-private/evidence"
```

The validator checks exact 60-case cardinality, source SHA formats, pinned Semwright SHA, UTC event order, independent reviewer distinct from executor, PASS rationale, actual evidence file existence/size/SHA-256, no symlink traversal, no path escape and bounded file sizes. Large files are hashed in 1 MiB chunks. Private media is **not** copied to the repository or uploaded by this tool.

**Important: identity fields are self-attested.** The validator cannot authenticate identities, prove a screenshot depicts the correct app, prove a video was actually reviewed, or tell whether a human truly followed the private scenario. Such judgments remain the independent reviewer's responsibility. A `PASS` in this private session does not automatically promote public `docs/acceptance/acceptance-status.json`; the maintainer must manually review the evidence and privacy/licensing constraints before publishing any safe pointer. The source/Semwright SHAs need to match the exact tested binaries; the tool verifies the lock and formatting but cannot attest binary signing or the provenance of an external installation.

The CI `source-policy` job runs synthetic, lightweight regression tests for the intake guardrails, including tampered bytes, forged/self-review, missing evidence, timestamps, path traversal and symlinks. Its green status is **not** one of the 60 PASS cases.

## Current state

At creation, all 60 canonical acceptance cases are `NOT_RUN`. The acceptance intake does not change that status. Product readiness still requires executable projects on each intended OS, end-to-end audiovisual output, real native rendering receipts, actual media review, correct asset/version handling and independent acceptance.
