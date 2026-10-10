# v0.5 attach-first: preserve real native source before attempting translation

**SRS:** `MW05-E11-01 — Attach primero`: link a pre-existing native
project/source without reducing it to a common denominator, separate
observable structure from typed controls and opaque capabilities, and
**never remove unknown effects during import**.

**Implemented candidate:** commit-specific, read-only inspection of
already imported, content-addressed project-owned assets. This deliberately
extends, rather than replaces, the existing `NativeCapsule` and
StudioService project revision/CAS architecture.

## Source path and editor workflow

1. An owner uses the existing desktop `import_asset_file` operation to
   select an original `.glb` or `.blend` from their machine, with the
   existing `import_local` effect grant. The service stores exact bytes
   in its content-addressed asset store; `.glb` is recognized as
   `model/gltf-binary` and `.blend` as `application/x-blender`.
2. In **Production → Native sources**, the owner attaches that *existing*
   asset to a specific original scene as a `NativeCapsule`, preserving
   original asset ID, SHA-256 and declared source application.
3. The owner selects **Inspect persisted source** on the capsule. The
   desktop checks exact `project_id`/`generation`/`revision`, source
   capsule ID, asset membership and SHA-256 in the current project.
   It reads only the authorized CAS blob with a hard 32 MiB inspection
   budget and rechecks project revision before showing the observation.
   It does not expose the source bytes to the browser and no arbitrary
   filesystem path, import command, rendering program or network request
   can be supplied by a model.
4. The UI separates **structured observable** GLB JSON counts
   (scenes/nodes/meshes/materials/animations/cameras/etc.) from the
   original **opaque** binary chunks, `extras`, external glTF extension
   identifiers and unknown root fields. It preserves every original
   source byte in the CAS, with chunk offsets and digest-bound receipts.
   It does **not** normalize, convert, discard or silently re-export the
   original source. Real Blender binary projects are treated as opaque
   because their native effect semantics require Blender.
5. The existing portable project bundle can preserve content-addressed
   source blobs without granting the source application's runtime credentials.
   **Opaque blobs are not guaranteed to be free of embedded secrets or
   private media**: the owner must inspect rights and sensitive data before
   exporting or sharing a bundle. Portability to another machine and native
   semantic editing must be independently revalidated before production.

## GLB 2.0 admission constraints

The inspector requires exact MIME `model/gltf-binary`, GLB v2 magic,
declared file length, a single first JSON chunk, 4-byte chunk alignment,
bounded JSON/chunks and glTF `asset.version=2.0`. It parses the
**first-party observation surface** only: numerical counts and
declared extension names. Unknown extensions, nested effect metadata,
binary buffers and additional non-JSON chunks are explicitly **opaque
preserved** rather than interpreted as editable renderer controls.
Malformed glTF, extra JSON chunks, forged source digests, unsafe source
identity, oversized files and unbounded/deceptive extension collections
are rejected as an *inspection*; the original owner-attached source is
never modified or deleted.

The receipt deliberately states:

- `semantically_editable_by_motionwright = false`
- `runtime_or_imported_code_executed = false`
- `native_renderer_fidelity_observed = false`
- `project_snapshot_was_modified = false`
- `human_creative_approval = false`
- `external_rights_or_install_granted = false`

A GLB may contain observable mesh/camera/animation records **without**
proving that Motionwright can faithfully edit those structures, preserve
proprietary material/shader semantics, run external extensions, render
their exact pixels or manage their animation timing.

## Verification and limitations

The isolated CI lane `.github/workflows/v05-attach-first.yml` validates
Rust/Clippy/negative paths and also builds an **actual original Blender
stage** in disposable CI, exports its real GLB and then runs the read-only
first-party inspection on those exact bytes. The same file SHA appears in
the Blender source export receipt and the attachment observation; no
synthetic fake GLB is substituted for that real integration gate.

Unit tests additionally include intentionally unknown source chunks,
proprietary root metadata and custom extensions and confirm all are
reported as opaque without code execution or semantic editability.

**Not completed:** proprietary Blender source introspection, real editing
of externally authored GLB geometry/animation/materials in Motionwright,
fully lossless semantic import of unknown effects, arbitrary source
plugins, rights verification, human creative review and a native
round-trip through the source application. The contract and real GLB
observation improve `MW05-E11-01`, but the requirement remains
`PARTIAL` and `release_acceptance: NOT_CLOSED`.
