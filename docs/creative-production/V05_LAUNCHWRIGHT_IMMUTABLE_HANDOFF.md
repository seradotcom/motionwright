# Motionwright v0.5 → Launchwright: immutable source/material handoff

**Requirement:** `MW05-E14-04 — Handoff Launchwright` in the supplied
v0.5 SRS delta. Preserve source revision, SHA-256, claims, rights and
approval; Launchwright may package/channel the material without mutating
creative timeline. Preparing material **does not authorize sending or
publishing it**.

## Implemented scope

`tooling/launchwright-handoff/export_handoff.py` produces a deterministic,
self-contained ZIP with:

- The **exact bytes** of the current original Motionwright Project JSON
  snapshot, including its revision, generation, and authored timeline.
- The existing, already-rendered MP4/MOV/Matroska master, SHA-256 verified
  against the same Project's native `HandoffBinding` of kind
  `launchwright / artifact_output / artifact`.
- `handoff.json`: versioned public binding, source SHA, project generation and
  revision, ProductionPlan approval fingerprint, declared verified claims
  and asset rights, plus explicit **no publication authority**.

Only this bounded, media/data-only artifact is created. No editor changes
occur; the ZIP test reopens the source and proves the project and media bytes
are **identical**. Any redaction, variant production, release/target binding,
channel packaging or publishing remains the receiving product's responsibility.

This follows the actual source concepts:
`crates/domain/src/integrations.rs` contains the revisioned
`HandoffBinding` with `system:launchwright` and exact digest, while the
Project/ProductionPlan owns claims, shot assets and owner-reviewed design.
Launchwright has its own MediaPlan, Release/Target/Scenario, source pins and
publishing gates. Do not duplicate those in Motionwright.

## Run after the owner has approved an actual project/clip

Export a **current exact-revision Project JSON** from the native workstation.
Create an output HandoffBinding via the existing integrations panel and
commit it through the canonical project change service. The binding must
name a public Launchwright artifact and carry the **exact same SHA** as the
actual completed video master. Confirm the saved project snapshot includes
that committed binding.

Construct a small request file:

```json
{
  "schema": "motionwright.launchwright-handoff-request/1",
  "mode": "real_work",
  "project_snapshot": {
    "path": "project/snapshot.json",
    "sha256": "<64 lowercase SHA-256 hex digits>"
  },
  "master_video": {
    "path": "output/master.mp4",
    "sha256": "<64 lowercase SHA-256 hex digits>"
  },
  "expected_project": {
    "id": "<existing UUID>", "generation": "<existing UUID>", "revision": 12
  },
  "handoff_binding_id": "<committed native HandoffBinding UUID>",
  "asset_rights": [{
    "asset_id": "<shot media UUID>", "sha256": "<asset SHA-256>",
    "owner": "Original source owner", "usage_rights": "Authorized usage terms",
    "authorized": true
  }],
  "claim_reviews": [{
    "claim_id": "<actual production shot claim UUID>", "verified": true,
    "source_note": "Source/provenance actually reviewed by the owner"
  }],
  "owner_review": {
    "reviewer": "<same actual ProductionPlan approver>",
    "approval_content_sha256": "<native plan approval fingerprint>",
    "creative_approved": true,
    "publication_requested": false
  }
}
```

The example placeholders are intentionally **not executable inputs**.
Populate them from actual source artifacts. All planned media assets must
appear in `asset_rights` (or the list is empty if the approved plan has no
source assets), and all claims used by planned shots must have a matching,
actually sourced review. Do not fabricate `verified:true` for synthetic
demonstrations or unrelated claims.

Run:

```bash
python3 tooling/launchwright-handoff/export_handoff.py \
  /path/to/request.json \
  --root /path/to/OWNER_AUTHORIZED_SOURCE_FILES \
  --output /path/to/NEW-motionwright-launchwright-handoff.zip
```

The script refuses stale project generations/revisions, missing real
ProductionPlan approval, a binding targeting another product, media hash
mismatch, unapproved claims/rights, unknown fields, malformed file formats,
symlinks, traversal and any request that asks to publish automatically.
It never executes project scripts and has **no network or Launchwright
credentials**. Zip timestamps/order are fixed for reproducibility.

The companion independent receiver checker validates the **actual output ZIP**
against its manifest. This catches any on-disk source mutation between initial
SHA admission and packaging, rejects additional archive members or symlink
entries, verifies both original project and media bytes, and confirms the
embedded Launchwright binding belongs to the original snapshot. The exporter
runs this verification before reporting a candidate ready, while
`verify_handoff.py` can be invoked separately by a future receiving adapter:

```bash
python3 tooling/launchwright-handoff/verify_handoff.py /path/to/handoff.zip
```

It returns only `byte_identity: PASS` and `launchwright_import: NOT_RUN`.
That is **not** an authenticated handoff, accepted Release, audio/video quality
result or publication authorization.

## What remains unfinished

The protocol creates an immutable Motionwright-side candidate, not an
accepted Launchwright Release. Launchwright still must independently import
and verify the package, resolve its own public Release/Target/Scenario source
identities, validate claims against its own evidence, run its MediaPlan
contract and receive explicit publish approval. The validator does not
decode the media master or independently authenticate the approving person.
These are separate quality/security gates.

**Current result:** scoped packaging contract and negative-path tests,
**not a live inter-product E2E** and not yet `MW05-E14-04` release acceptance.
