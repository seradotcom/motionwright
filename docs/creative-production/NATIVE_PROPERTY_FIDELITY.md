# MW05-E04-02 — Native source property fidelity negotiation

This is a real, editable **project-source metadata contract**. It is not a
conversion engine and never replaces, bakes, transforms or executes an
imported source. The source remains an immutable, content-addressed asset
linked to a NativeCapsule, a scene and the project generation and revision.

## Owner workflow

Under Production → Native sources, attach an already imported source
using the existing revisioned native capsule operation. The original
aggregate visual, temporal, structural and editability report remains.

The Property-level fidelity negotiation panel lets the editor:

1. Select a stored capsule and see the SHA-256 source fingerprint and
   current project revision.
2. Enter a semantic path such as camera.focal_length,
   animation.keyframes or materials.base_color. Paths contain at most
   five dotted ASCII identifiers and 128 bytes; no shell paths,
   commands, magic keys or executable permissions.
3. Classify one property as native, translated, baked, approximate
   or unavailable. Lossy/unavailable outcomes require an explicit
   human-authored explanation.
4. Commit the record through the **existing** upsert_native_capsule
   operation and its StudioService compare-and-swap revision using
   the pinned Semwright Native SDK.
5. Inspect, change or remove classifications on later revisions.
   No renderer effect, property edit or import conversion is implied.

The classification is an operator declaration, not independently
verified source/property fidelity. A value marked native is not proof
that an arbitrary renderer preserves it. The capsule editable_parameters
list and aggregate fidelity.evidence_sha256 remain untouched by this
operation. The user cannot grant provider execution through this panel.

## Persistence, compatibility and source security

NativeCapsule.property_fidelity is an optional typed, bounded list.
Legacy native capsules deserialize with an empty list. Empty lists
serialize without changing the original JSON representation.

The trusted Rust domain rejects duplicate or malformed semantic paths,
oversized lists or notes, control characters, any baked/approximate/
unavailable classification without a loss explanation, and references
whose imported immutable source or scene identity no longer matches.

Studio mirrors the validation for immediate feedback. Native SDK calls
still go through canonical CAS. A stale competing client cannot apply
a classification to an obsolete project revision. Source bytes, object
IDs, timing, source assets and actual editable-parameter grants do not
change when classification metadata is revised.

## Verification and remaining v0.5 scope

Rust tests reject negative cases and verify legacy schema round-trip.
A real minimal binary GLB is imported through the ordinary service
CAS flow, its capsule classified, SQLite reopened, and the original
GLB bytes compared. Browser tests exercise record/edit/reopen,
content-addressed SHA preservation and rejection without mutation.

The original v0.5 requirement remains partial: no provider-specific
property translation, renderer execution, independently measured native
property readback, source round-trip modification, lossless output
guarantee or human creative acceptance is established by this step.

The source-pinned Semwright SDK is unchanged. The 60 original product
acceptance cases remain NOT_RUN.
