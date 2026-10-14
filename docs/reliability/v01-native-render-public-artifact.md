# Motionwright v0.1 — publicly distributable native render evidence

**Scope:** v0.1 CI transport and original Motion Canvas render verification.
This does not change native execution, the Semwright Native SDK pin,
destructive grants, project lifecycle, or creative features.

## Problem

The v0.1 real Motion Canvas CI workflow uploaded the entire internal
verification/native-render-e2e directory even on failure. That directory
includes semwrightd.log, original production request/response receipts,
provider lifecycle samples, source-native job identities and private owner
paths. Those diagnostics are not required by someone inspecting rendered
frames and should not be downloadable by default.

## New closed public artifact

The workflow continues to run and independently check all original
source-bound native evidence **inside the disposable CI runner**. After
all checks it runs tooling/native_render_public_artifact.py, creating a
separate directory that is the only uploaded artifact source.

On a successful CI run, the public artifact contains:

- frame-first.png, frame-middle.png and frame-last.png: actual mediated
  native review images, each cross-checked against the verified native
  render result digest;
- four direct-native-frame-XXXXXX.png images at 0, 15, 30 and 59;
- four mediated-native-frame-XXXXXX.png images at 0, 15, 30 and 59;
- artifact-manifest.json, hash-bound to the original mediated rendering;
- public-evidence.json, listing 12 file sizes and SHA-256 values and
  exact Motionwright/Semwright source revisions.

The publisher separately validates original result and direct-vs-mediated
parity receipts, same owner project generation and revision, same canonical
Film digest, 60 frames per render, 7 or more direct native checks, complete
source-bound RGBA identity and the four selected frame numbers. PNG file
container bytes need **not** be identical even when decoded RGBA pixels are:
the report does not invent a PNG bitwise-parity claim. All original private
job IDs, owner directories, daemon logs, rendered Film request and detailed
lifecycle samples remain outside the public directory.

A failed, cancelled or skipped CI run can publish **only** a NOT_VERIFIED
public receipt without any media. It does not turn the failure into a
successful job, retry the mutation or request new grants. The public receipt
explicitly records no original-user acceptance, no provider-generation
reliability repair and no stable release permission.

## Verification and limitations

The new Python suite covers source/pin or generation swaps, incomplete
parity, forged human approval, altered native manifest/review hashes,
private-looking manifest fields, linked media files, duplicate JSON keys,
invalid CI statuses and public upload allowlist enforcement.

The publication tool was also tested against an immutable prior real
source-bound native Motion Canvas CI result, passing the exact 12-file
public allowlist and SHA accounting. The new exact-head full GitHub
Actions run remains mandatory before merging.

A source-bound native CI success is not a first-owner physical installation,
human aesthetic acceptance, a production reliability proof, or
authorization for v0.1.0 release. Issues #49, #89, #91 and #126 remain
independently blocked.
