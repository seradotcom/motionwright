# Motionwright v0.1 — curated Blender / Manim native CI artifacts

**Scope:** Evidence publication for the original v0.1 real native Blender and Manim
round-trip workflows. This does not change project state, native execution,
Native SDK source pin, effect grants, renderer lifecycle or any v0.5 feature.

## Risk and correction

Both v0.1 workflows formerly uploaded their entire internal verification
folders. Real successful CI outputs contained semwrightd.log, sandbox-profile.txt,
run.log, result.json and original motionwright-*-evidence.json receipts.
Those receipts carry owner-scoped native references and session information.
They are not appropriate for public artifact distribution.

The original Broker/Driver Host verification and media shape checks still run
inside the disposable CI runner. The publisher only reads the previously
verified bytes and makes **no native call**.

On a successful run:

- Blender publishes motionwright-contribution.glb and public-evidence.json.
- Manim publishes motionwright-manim-community.mp4 and public-evidence.json.
- Both reject an incorrect Motionwright SHA, a different pinned Semwright
  revision, inconsistent original project generation/revision/scene identity,
  incomplete native command receipts, mismatched SHA-256 and size, and invalid
  GLB/MP4 container signatures.
- A source- and pin-bound public receipt lists the single media file, byte
  size and SHA-256; it explicitly excludes original project/job/session IDs,
  private native receipts, daemon logs and original sandbox configuration.
- Failure, cancellation or skip publishes only NOT_VERIFIED and **zero media**,
  never an invented success; if successful evidence is missing or malformed,
  publication fails closed instead of accepting a broken native product.

No original native authority, session material or secret-bearing diagnostic
is copied into the new public artifact folder. A successful automated render
still cannot attest owner-installed acceptance, visual design approval or a
signed release.

## Verification

The publisher was tested against **two independently downloaded successful
GitHub Actions native artifacts** on identical source revision
bb36991bfddb147f45aa66256093f07a33f7de97 and pinned Semwright
04142a2ae16e53a58bb6cf43135786e396a4b720.

It produced exactly two public files for Blender and two for Manim; both
copied media digests independently matched their original native source
receipts and file sizes. Temporary copies of the original private CI
folders were removed after verification. Unit tests also exercise integrity
swaps, foreign source/revision and negative CI outcomes.

The new exact-source GitHub Actions run is required before merging.
The native render CI, issue #49 provider-generation reliability, issue #91
foreground-authorized MLT cleanup, issue #89 glib advisory, and independent
owner acceptance issue #126 remain separate and cannot be closed by this
publication-only change.
