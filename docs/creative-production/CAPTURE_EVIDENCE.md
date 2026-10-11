# Reproducible first-party screen capture · MW05-E07-02

Implementation status: **partial / bounded**. This feature does not yet
provide a general-purpose capture of arbitrary customer applications.

## Captured application and trust boundary

A disposable Chromium session captures the **running Motionwright Studio**
application from a strictly local origin. The test uses deterministic,
synthetic first-party fixture state; no real customer accounts, cloud
sessions, credentials, tokens, or third-party websites are admitted.

The capture records:

- The exact source commit and Studio package version.
- The loopback origin, relative route, and ordered navigation actions.
- An exact Chromium viewport of 1440 × 940 at device scale factor 1.
- The authored synthetic project ID, generation and revision, including one
  claim linked to the exact project revision.
- A SHA-256 digest of the fixture state actually supplied to the application.
- An explicit single-element redaction selector and its pixel rectangle.
- SHA-256 of the masked screenshot and of a second screenshot obtained by
  replaying the documented scenario.

Browser routing blocks external HTTP(S) requests; a visible password field
fails acceptance. Playwright uses a new isolated browser context with cleared
cookies. No unmasked screenshot is saved.

The reproducible screenshot is **not** an observed third-party product
interaction. A claim linked to the synthetic fixture project says only that
the Studio Production workstation is visible in the approved local browser
scenario. This is a test of the capture pipeline, not evidence of a product
benefit, customer use, software integration or marketing outcome.

## Independent proof and redaction

The standard-library verifier at tooling/capture_evidence.py is independent
of Playwright and checks the receipt and actual PNG bytes. It verifies exact
source/version/claim/data hashes and refuses undocumented URL/viewport, extra
fields, credentials, external network requests, nonrepeatable screenshots
and missing or oversized redactions.

The verifier decodes 8-bit RGB/RGBA PNG scanlines (including PNG row filters)
itself, validates chunk CRCs, checks several decoded interior mask pixels
against the declared opaque mask color and requires that the unmasked image
area contain actual diverse application pixels. It is **not** OCR, a
copyright/identity judgment or visual-composition approval.

A CI workflow runs the original first-party capture twice, independently
verifies the resulting PNG, and uploads its source-state/claim receipt and
redacted screenshot. Adversarial tests ensure incorrect checksum, forged
source revision, wrong claim, stripped redaction, excessive masking, inserted
network or credential evidence, and altered data state fail closed.

The workflow's exact commit and artifact are required before technical
acceptance. A green test from an earlier commit cannot certify a later
revision. The review status remains "technical evidence only; human review
required".

## Persistent capture-source registration

An admitted first-party capture may also be registered in the existing
project-local StudioService journal with Change::RegisterCaptureSource
(capture-source.register through the same Semwright Native SDK app). The
first-party source-revision claim itself uses the existing append-only
Change::AddClaim, surfaced as claim.add in the same SDK app. It does
**not** execute or ingest screenshots by itself. The caller must first
import the redacted PNG, JSON capture receipt and original fixture-state JSON
through the ordinary immutable Asset store, then reference all three Asset
IDs with exact SHA-256 digests and an already recorded brief claim
bound to that project's source revision. The originating project generation
must match at registration. For portable project imports, the historical
generation remains in the record after the application's import procedure
rotates the current generation.

The Production ? Capture sources workstation now allows the editor to
author a bounded first-party claim, select previously imported PNG and JSON
source assets, including the separately imported original fixture-state JSON,
and supply the source revision, app version and mask geometry. The source
fixture's SHA-256 comes from its imported Asset rather than a typed-in value. The panel does not read bytes, capture the desktop,
or certify privacy. Native SDK/StudioService and the separate PNG evidence
verifier remain authoritative.

The record has a fixed first-party origin, app, scenario, versioned viewport,
mask selector, no external-request or credential-field allowance and a
strict bounded schema. It cannot carry raw credentials, arbitrary URL/cookies,
a third-party capture permission or a fabricated verification flag.
The PNG, JSON receipt and original fixture-state Asset cannot be removed
while referenced by the append-only capture record. Reusing the same registration ID is prohibited;
replaying an identical durable request should return its prior CAS receipt.

The SQLite admission now checks the actual imported **source bytes** after
idempotent request replay, owner-generation and revision authorization.
Its 64 KiB strictly parsed JSON receipt must match the declared application
source commit/version, loopback URL, exact browser actions, 1440 ? 940 viewport,
synthetic fixture digest, linked project claim, one redaction rectangle, and
the digest of the repeated masked screenshot. The immutable source PNG is
read and SHA-256 verified independently and must contain a valid PNG IHDR
with the exact admitted viewport and checksum; a different bitmap cannot
be substituted under the declared identity.

The exact source fixture-state JSON is now the **third independently
imported immutable Asset**. SQLite reads its actual bytes and checks the
original project ID, generation, revision, unique claim ID, claim text,
source reference, source-revision label and editorial context against the
persisted project and receipt. A captured dataset cannot be silently
replaced with another file that merely declares the same manifest fields.

This establishes bounded **source-file correspondence**, not automatic
truth, license ownership, or redaction effectiveness. Actual decompressed
PNG pixels, mask coverage and screenshot repeatability remain independently
inspected by Chromium and the PNG evidence verifier. Registering these
assets does not certify rights, privacy clearance, customer activity or
creative approval.

SQLite negative tests reject a forged but genuinely imported JSON receipt,
a genuinely imported PNG with wrong viewport dimensions, and forged source
state whose claim text or original project generation is contradicted by
the persisted project. These tests do not modify the durable project state. The
tests use an explicitly synthetic 1440 ? 940 PNG raster under tooling/testdata,
not a claimed real browser capture.

Portable .motionwright bundle acceptance exports all three imported source
Assets, their immutable capture-source registry entry, and the exact authored
claim. Import into a fresh project rotates the current generation but retains
the historical capture generation and original blob digests without rewriting
source provenance. Duplicate admission after import remains rejected. This
is a portability contract, not external window capture or media playback.


New source-contract tests exercise source-identity mismatch, future or
foreign revision, incorrect digest, missing evidence asset, redaction
tampering, project locks, source removal and durable SQLite replay.
The original first-party browser capture workflow remains a separate
technical test, not a substitute for user-authorized external capture.

## Explicit remaining product work

- Exact capture of a user-authorized external application or native desktop
  window, including version, dataset/revision and owner permission.
- Multi-action scenario scripting with reproducible input fixtures.
- Recorded redaction policies spanning all sensitive input sources.
- User-facing admission and revalidation of external source captures and
  privacy policies. First-party PNG/JSON registry metadata is now modeled;
  complete external capture-to-asset workflow remains outside this slice.
- Authentic playback and asset replacement in ProductStage, and renderer
  receipts linked to the production timeline.
- Human rights and creative review of actual customer material.

The existing native Semwright broker and application-owned project authority
remain the integration path. No new provider, privileged screenshot access
or unrestricted browsing capability is enabled by this initial proof.
