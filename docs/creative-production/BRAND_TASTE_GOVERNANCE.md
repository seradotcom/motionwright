# MW05-E06-01 · Brand policy and taste governance (bounded implementation)

This slice adds project-local, revisioned controls to the existing Motionwright
project and Production Studio. It is not an organization-wide policy service.

## Authority model

- **BrandProfile** stores mandatory, typed prohibitions and constraints:
  forbidden case-insensitive copy phrases, allowed hero accent colors and a
  required hero wordmark. Rules are identified by stable UUIDs, and a modified
  profile with the same ID must increment its version.
- **TasteProfile** stores preferences by axis and labels each as inferred or
  reviewed. Preferences are advice, never exceptions or authorization to modify
  brand policy.
- **CreativeDecision** records a scene-local choice, rationale and declared
  author through the same project revision history.
- **BrandException** is bound to one rule UUID, one existing scene UUID and the
  SHA-256 digest of the exact current profile. Each records a campaign,
  rationale and declared author. Updating the policy invalidates old waivers.
  There is no global/unscoped waiver operation.

The validator checks *both* current human-edited scene node text/styles and
the component's persisted authored configuration. Applying a brand policy and
its waivers is a single project mutation, with existing revision and content
locks. A waiver on scene A never applies to scene B. A preference that requests
prohibited copy still fails when that copy is used in a scene.

## Scope and limitations

The profile lives in the project; organization-level distribution, signature
verification, directory/IAM integration and delegation of approval authority
are **not implemented**. The author label is provenance supplied by the
editor, **not cryptographic proof** of identity or rights clearance.

For this vertical slice, palette and wordmark restrictions apply to authored
first-party hero components; phrase restrictions apply to scene nodes and hero
copy. In MetricEvidence, the large headline is also an accent-painted semantic
field: the allowlisted palette governs its current authored fill after human
style edits, not only the component config, wordmark and graphic rule. Generic fonts, imagery, external-native payloads and audio are not yet
subject to full brand compliance. This does not certify a delivery as brand-safe.

Adding a rule to an already nonconforming scene fails closed until the text is
corrected or a matching scene waiver is atomically supplied. The current
Studio UI handles policy creation and later waivers but does not yet provide
a combined one-click migration of a nonconforming legacy composition.

Tests: `crates/domain/tests/brand_governance.rs` and
`apps/studio/src/brandGovernance.test.ts`; full CI runs on the dedicated PR.
