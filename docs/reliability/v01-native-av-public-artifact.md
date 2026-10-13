# Motionwright v0.1 — public native AV master evidence boundary

**Scope:** Source-bound, CI-only transport of real Motion Canvas → MLT native AV
acceptance evidence. This neither changes native render execution nor authorizes
the 0.1.0 release.

## Why the original artifact upload was unsafe

The private native AV evidence directory contains the disposable Broker daemon
log, detailed Motionwright native render receipts, project/runtime paths, process
lifecycle samples and original job-status observations. These are useful for
controlled failure triage but need not be present in downloadable public
GitHub Actions artifacts. The original workflow uploaded the entire directory
even when a native render failed.

## Bounded public delivery

The workflow continues to generate and independently validate **all original**
evidence inside the disposable CI runner. Only after verification does
`tooling/native_av_public_artifact.py` create a new public-only directory.
A successful run admits exactly six source files:

- `motionwright-master.mp4` — the actual 60-frame H.264/AAC master;
- `frame-first.png`, `frame-middle.png`, `frame-last.png` — real decoded visual samples;
- `artifact-manifest.json` — native content evidence without owner references;
- `decoded-media-proof.json` — bounded decoded video/audio acceptance metrics.

A separately generated `public-evidence.json` lists each admitted file's size
and SHA-256 digest, the exact Motionwright/Semwright source identities and explicit
no-owner-acceptance/no-release claims. No original `result.json`,
`motionwright-render-evidence.json`, `semwrightd.log`,
`native-provider-lifecycle.json`, private session files or raw process receipts
are uploaded.

The publisher checks the trusted CI job outcome, native PASS receipt, exact
source and SDK SHA, frame accounting, both native manifest/master digests, decoded
AV evidence, media format headers and a closed file allowlist. Invalid,
linked, oversized or private-looking source material is rejected before any
public file is written. A failed or cancelled job publishes **only** a
`NOT_VERIFIED` public receipt, never an unverified MP4 or secret-bearing log.

## Verification and limits

Local adversarial tests cover independent source and media digest mismatches,
malformed container media, private manifest fields, decoded-frame
misrepresentation, missing files, failed-run redaction and prevention of a
return to the original blanket artifact upload. A representative successful
pinned AV CI artifact (run 38457981268, source a510fecc) was repackaged
locally using this new guard as a compatibility smoke; that observation is
not a substitute for the **new exact-head workflow run**.

CI keeps the original private source files only in its ephemeral filesystem;
this change does not retroactively remove older GitHub artifacts already
published. It does not fix Semwright provider-generation loss (#49),
authorize destructive MLT closure (#91), eliminate transitive GTK/glib
warnings (#89), execute the 60 independent product acceptance cases, or
grant permission to publish a stable Motionwright v0.1 release (#126).
